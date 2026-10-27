# pyjustwannabepure for Zed

Runs the `pyjustwannabepure` language server on Python files.

Install it from Zed with `zed: install dev extension` and pick this directory.

The extension does not install the server. Put it on your `PATH`:

```console
$ uv tool install 'justwannabepure[lsp]'
```

or point Zed at a specific binary in `settings.json`:

```json
{
  "lsp": {
    "pyjustwannabepure": {
      "binary": {
        "path": "/path/to/project/.venv/bin/pyjustwannabepure",
        "arguments": ["server"]
      }
    }
  }
}
```

If you have customized `languages.Python.language_servers`, include
`"pyjustwannabepure"` (or `"..."`) in that list.
