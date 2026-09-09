# Contributing

Thank you for your interest in contributing to this project!

日本語版: [CONTRIBUTING.ja.md](CONTRIBUTING.ja.md)

## How to contribute

### Reporting issues

- Search [existing issues](../../issues) first to avoid duplicates.
- For bug reports, include steps to reproduce, expected vs. actual behavior, and your environment (OS, Blender version, etc.).
- Feature requests and questions are welcome as issues too.

### Pull requests

1. Fork the repository (external contributors) or create a branch from `main` (team members).
2. Make your changes on your branch.
3. Open a pull request against `main` with a clear description of what and why.
4. A code owner will review your PR. Please respond to review comments.
5. PRs are merged by squash merge, so feel free to keep work-in-progress commits on your branch.

Direct pushes to `main` are disabled; all changes go through pull requests.

## Rules

### No secrets or private data

Never commit any of the following:

- **Credentials** — API keys and tokens (e.g. strings like `sk-...` or `ghp_...`),
  passwords, private keys, `.env` files.
- **Personal data** — real names, email addresses, body scans or measurement
  data of real individuals, or any other personally identifiable information.
- **Internal or customer material** — internal-only documents, customer data,
  or anything else not meant to be public.

Every pull request is scanned by
[gitleaks](https://github.com/gitleaks/gitleaks) in CI, and merging is blocked
while the scan fails. Note that gitleaks only detects credentials — personal
data and internal material are **not** caught automatically, so double-check
your files before committing. If you accidentally commit a secret, revoke it
immediately — removing it from the branch is not enough once it has been pushed.

### Large files

Keep binary assets small. Files over 50 MB trigger warnings and files over
100 MB are rejected by GitHub. If your contribution requires large 3D assets,
please open an issue first to discuss how to handle them.

## For team members

Internal contributors: see the [team guide (Japanese)](docs/team-guide.ja.md)
for the internal workflow and rules.

## License

By contributing, you agree that your contributions will be licensed under the
same license as this project (see [LICENSE](LICENSE)).
