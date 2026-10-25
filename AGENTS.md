# Instructions for AI agents

Read this before changing anything. It applies to any AI coding agent working in this repository. `README.md` and `CONTRIBUTING.md` (when present) hold the project-specific rules; where they conflict with this file, they win.

## Before you start

- Read the README, any roadmap or changelog, and the open issues and pull requests. Check whether the work already exists or is already tracked.
- Look at `.github/workflows/` to see what CI runs, and run those same checks locally before you push.

## Keep the docs true to the code

- Update the README, roadmap, changelog and any other docs in the same change as the code they describe.
- When you finish something tracked by an issue, check off the matching roadmap item and close the issue only if every point in it is done. Use one `Closes #N` per issue; a line like `Closes #1 and #2` closes only the first.
- If you notice a roadmap item or issue that is already done, or no longer true, say so and fix it. Don't leave stale entries.

## Test it for real

- Run the project's own tests, linters and build. For anything with a UI, run it in a real browser or simulator instead of relying on reading the code, and check small screens, dark mode and printing where they apply.
- Add or extend a test for each behavior you add or fix.
- Only report a check as done if you actually ran it. Say plainly what you could not run.

## Pull requests

- Branch from the default branch and base the pull request on it. Don't stack pull requests on other pull request branches.
- Use the repository's pull request template if there is one, and tick only what was really done.
- Wait for CI to pass before calling a pull request ready.
- Don't merge, force-push or bypass a blocked action without the owner's permission. If something is blocked, report it instead of working around it.
- Don't create issues, pull requests or changes in other repositories without asking.

## Don't

- Don't commit secrets, tokens or personal data.
- Don't add dependencies or network calls the project doesn't already use without asking.
- Don't make unrelated changes in the same pull request.
