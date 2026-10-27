# pyjustwannabepure for Zed

Runs the `pyjustwannabepure` language server on Python files.

Install it from Zed with `zed: install dev extension` and pick this directory.

The extension does not install the server. It looks for it, in order:

1. `lsp.pyjustwannabepure.binary.path` in your Zed settings
2. `pyjustwannabepure` on your shell's `PATH` (e.g. `uv tool install 'justwannabepure[lsp]'`)
3. the active virtualenv (`$VIRTUAL_ENV`)
4. the project's `.venv`, e.g. after `uv add 'justwannabepure[lsp]'`

To point Zed at a specific binary, add this to `settings.json`:

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
