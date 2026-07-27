# Official technical references

The skill design follows these upstream documents:

- Forgejo API usage and authentication: https://forgejo.org/docs/latest/user/api-usage/
- Forgejo access-token scopes: https://forgejo.org/docs/latest/user/authentication/token-scope/
- Forgejo repository creation and first push: https://forgejo.org/docs/latest/user/getting-started/first-repository/
- Forgejo push-to-create behavior: https://forgejo.org/docs/latest/user/git-cli/push-to-create/
- Git push documentation: https://git-scm.com/docs/git-push
- GitHub’s vendor-neutral OpenSSH workflow examples for checking, generating and adding SSH keys:
  - https://docs.github.com/en/authentication/connecting-to-github-with-ssh/checking-for-existing-ssh-keys
  - https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent
  - https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account

The target instance is configured by default as `https://www.synnovator.com`; its API page identifies the service as Forgejo. Instance-specific SSH hostname, port, branch protection and token policy may be configured differently by the operator, so the script exposes SSH host/port overrides and stops rather than bypassing policy.
