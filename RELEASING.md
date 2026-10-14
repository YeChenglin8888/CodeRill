# Releasing CodeRill to PyPI

The release workflow builds and checks both distribution formats, then uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) to upload
without a stored API token. Publishing is triggered only by a `v*` Git tag.

## One-time account setup (repository owner)

1. [Register a PyPI account](https://pypi.org/account/register/), confirm its
   email address, enable two-factor authentication, and store the recovery codes.
   Do not share the password or codes in issues, chats, or the repository.
2. In GitHub repository **Settings → Environments**, create `pypi` and require
   a reviewer for deployment. This keeps the upload step behind a human approval.
3. On [PyPI account publishing settings](https://pypi.org/manage/account/publishing/),
   add a **pending GitHub Actions publisher** with exactly these values:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `coderill` |
   | Owner | `YeChenglin8888` |
   | Repository | `CodeRill` |
   | Workflow filename | `publish.yml` |
   | Environment name | `pypi` |

The pending publisher does not reserve the name. Confirm that PyPI accepts it
before tagging a release. No TestPyPI account or long-lived API token is needed
for this release path.

## Each release

1. Update `version` in `pyproject.toml` and `__version__` in
   `coderill/__init__.py` to the same value.
2. Run `pytest tests/ -q` and `ruff check .`, review the release commit, and
   push it to `main`.
3. Create and push the matching tag, for example, for version `0.1.1`:

   ```bash
   git tag -a v0.1.1 -m "CodeRill 0.1.1"
   git push origin v0.1.1
   ```

4. Check the **Publish to PyPI** workflow. Approve its `pypi` environment only
   after its build and checks pass. The tag and both code version fields must
   match; the workflow fails before upload if they differ.
5. Confirm the package page at `https://pypi.org/project/coderill/`, then test
   `pipx install coderill` and `coderill --version` in a clean environment.
