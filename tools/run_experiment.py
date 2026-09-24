"""Causal frame-prefix evaluation; no model dependencies run locally."""
import base64
import json
import os
from pathlib import Path
import time
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/box-falls'
PROMPT = '''Analyze this chronological sequence of video frames, ending at the stated cutoff. You do not know what happens next. Monitor any object that might lose support and fall from a surface to the floor. An object already on the floor is not by itself a fall. Distinguish visible imminent risk from an observed fall. Do not assume a fall must occur. Consider support, overhang, motion, and hands holding objects. Respond only with JSON: {"action":"observe|warn_risk|alert_fall|uncertain","object":"...","evidence":"short explanation in Spanish based on visible evidence","fall_observed":false,"imminent_fall_risk":false}. warn_risk means a warning before a fall is observed; alert_fall means visible falling or a fall established by the sequence. Use uncertain if the evidence cannot establish this.'''

def write_report(report):
    target = OUT / 'results.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    temp.replace(target)

def main():
    deployment = json.loads((OUT / 'deployment.json').read_text())
    endpoint = deployment['id']
    key = os.environ.get('RUNPOD_API_KEY') or tomllib.loads((Path.home()/'.runpod/config.toml').read_text())['default']['api_key']
    def api(path, payload=None):
        request = urllib.request.Request(f'https://api.runpod.ai/v2/{endpoint}/{path}',
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    manifest = json.loads((OUT/'frames.json').read_text())
    report = {'model': 'Qwen/Qwen3-VL-8B-Instruct', 'endpoint': endpoint,
              'prompt': PROMPT, 'sampling_fps': 2, 'status': 'running', 'runs': [],
              'note': 'Prueba de un solo video; tiempo del video y latencia real se miden por separado.'}
    write_report(report)
    active_job = None
    try:
        for cutoff in [2, 4, 6, 8, 10, 10.5, 11, 11.5, 12, 13.5]:
            available = [f for f in manifest['frames'] if f['time'] <= cutoff + 0.001]
            # Preserve recent movement; include initial context. Never include future frames.
            frames = available if len(available)<=16 else [available[0], *available[-15:]]
            content = [{'type':'text','text':f'Video observed only up to {cutoff:.2f} seconds.'}]
            for frame in frames:
                content += [{'type':'text','text':f"Frame at {frame['time']:.3f} seconds"},
                    {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode((OUT/frame['file']).read_bytes()).decode()}}]
            payload = {'input': {'openai_route':'/v1/chat/completions','openai_input':{
                'model':report['model'], 'messages':[{'role':'system','content':PROMPT},{'role':'user','content':content}],
                'max_tokens':300,'temperature':0}}, 'policy':{'executionTimeout':180000,'ttl':900000}}
            run = {'cutoff':cutoff,'frame_times':[f['time'] for f in frames], 'status':'submitting'}
            report['runs'].append(run);write_report(report)
            start=time.monotonic()
            job=api('run',payload);active_job=job['id'];run['job_id']=active_job
            while True:
                state=api('status/'+active_job);run['status']=state['status'];write_report(report)
                if state['status'] in ['COMPLETED','FAILED','CANCELLED','TIMED_OUT']:break
                if time.monotonic()-start>900:raise TimeoutError('Worker did not finish within 15 minutes')
                time.sleep(4)
            run['wall_seconds']=round(time.monotonic()-start,2);run['response']=state
            active_job=None
            if state['status']!='COMPLETED':raise RuntimeError(str(state.get('error',state)))
            output=state.get('output');output=output[-1] if isinstance(output,list) and output else output
            text=output['choices'][0]['message']['content'];run['raw_text']=text
            try:
                decision=json.loads(text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
                if decision.get('action') not in ['observe','warn_risk','alert_fall','uncertain']:raise ValueError('Invalid action')
                run['decision']=decision
            except (ValueError,AttributeError):run['parse_error']='Model response is not valid decision JSON'
            write_report(report);print(cutoff,text,flush=True)
        report['status']='completed'
    except Exception as error:
        report['status']='error';report['error']=str(error);print('ERROR',str(error),flush=True)
    finally:
        if active_job:
            try:report['cancellation']=api('cancel/'+active_job,{})
            except Exception as error:report['cancellation_error']=str(error)
        write_report(report)

if __name__=='__main__':main()
