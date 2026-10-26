# GitHub and user-controlled TestFlight

For each change: review the diff, run relevant checks, commit with a conventional
prefix, and push that commit. Report the branch and commit. Python-only camera
experiments do not produce a new iPhone binary.

For an iOS change, a GitHub push alone does not create a TestFlight build:

```text
reviewed source → signed release archive → binary upload → Apple processing
→ user selects the processed build and testing group in App Store Connect
```

The browser manages a build that has already been uploaded; it does not compile
the source. Xcode, Transporter, or a configured upload API/command uploads the
signed binary. An archive or successful local device build is not an upload.
You usually increment the build number for each new upload; an additional beta
build does not itself require a new marketing version or public App Store release.

The existing local app record notes identify **Sutskever / Vision Lab**, bundle
`com.vision.lab`, with historical upload **0.2.0 (2)**. Verify App Store Connect
before selecting a new unique build number. Local notes also say the internal
group had automatic distribution enabled. The user should disable that in the
browser before a future upload if every distribution must be a manual decision.
These are historical notes, not confirmation of the current Apple account state.

An example archive command (choose a new build number first):

```sh
BUILD=3
xcodebuild -project ios/VisionLab.xcodeproj -scheme VisionLab \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "local/releases/VisionLab-0.2.0-$BUILD.xcarchive" \
  CURRENT_PROJECT_VERSION="$BUILD" archive
```

This creates an archive only. After signing/export validation, upload using
Xcode Organizer → Distribute App → App Store Connect, or a separately configured
upload command. Wait for Apple processing before expecting browser availability.
Export/upload authentication is separate from GitHub authentication.

The release handoff should say exactly:

```text
GitHub: <branch>, <commit>, <link>
Review/checks: <what passed, what was not tested>
App: Sutskever / Vision Lab (com.vision.lab)
Version/build: <version> (<build>)
Archive: <absolute local path, if created>
Apple state: not uploaded / uploaded, processing / processed and available
Your browser step: select that exact build under TestFlight and your test group
```

Do not call a build available without confirming its processed state. The user
operates the browser and controls tester assignment and beta submission. External
testing can require beta review. Do not select a public release or submit the app
to public App Store review as part of an ordinary TestFlight change.

Reference: [Apple — Upload builds](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/).
