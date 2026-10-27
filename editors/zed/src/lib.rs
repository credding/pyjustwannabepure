use zed_extension_api::{self as zed, settings::LspSettings, LanguageServerId, Result};

const SERVER: &str = "pyjustwannabepure";

struct PyJustWannaBePure;

impl zed::Extension for PyJustWannaBePure {
    fn new() -> Self {
        PyJustWannaBePure
    }

    fn language_server_command(
        &mut self,
        _language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<zed::Command> {
        // A configured `lsp.pyjustwannabepure.binary` wins over whatever is on PATH.
        let binary = LspSettings::for_worktree(SERVER, worktree)
            .ok()
            .and_then(|settings| settings.binary);
        let args = binary
            .as_ref()
            .and_then(|binary| binary.arguments.clone())
            .unwrap_or_else(|| vec!["server".into()]);
        let command = binary
            .and_then(|binary| binary.path)
            .or_else(|| worktree.which(SERVER))
            .ok_or_else(|| {
                format!(
                    "{SERVER} not found on PATH; install it with \
                     `pip install 'justwannabepure[lsp]'` or set lsp.{SERVER}.binary.path"
                )
            })?;

        Ok(zed::Command {
            command,
            args,
            env: worktree.shell_env(),
        })
    }
}

zed::register_extension!(PyJustWannaBePure);
