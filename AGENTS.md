# Collaboration

- The user is an engineer and wants to participate in the code and math. Explain
  small, concrete steps, point to the relevant code and equations, and provide
  experiments where the user can change one parameter and predict the result.
- Review changes, run relevant checks, and push the task's commits to GitHub.
  Preserve unrelated local changes; do not silently include them in a commit.
- The user operates the browser for TestFlight distribution. Report the exact
  app, version, build number, Git commit, checks, and actual release state
  (built, archived, uploaded, processing, available). Do not claim that a GitHub
  push makes a TestFlight build available. Do not operate App Store Connect in
  the browser or assign a build to testers on the user's behalf.
- Check automatic internal distribution before uploading a future iOS build;
  uploading could otherwise distribute it before the user's browser decision.
- Keep camera credentials, API keys, and personal camera outputs out of Git.
