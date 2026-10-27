use zed_extension_api::{self as zed, settings::LspSettings, LanguageServerId, Result};

const SERVER: &str = "pyjustwannabepure";

struct PyJustWannaBePure;

/// The server inside a virtualenv, which Zed's PATH lookup misses unless the venv is activated.
fn venv_binary(venv: &str) -> String {
    match zed::current_platform().0 {
        zed::Os::Windows => format!("{venv}\\Scripts\\{SERVER}.exe"),
        _ => format!("{venv}/bin/{SERVER}"),
    }
}

impl zed::Extension for PyJustWannaBePure {
    fn new() -> Self {
        PyJustWannaBePure
    }

    fn language_server_command(
        &mut self,
        _language_server_id: &LanguageServerId,
        worktree: &zed::Worktree,
    ) -> Result<zed::Command> {
        let env = worktree.shell_env();
        // A configured `lsp.pyjustwannabepure.binary` wins over whatever is on PATH.
        let binary = LspSettings::for_worktree(SERVER, worktree)
            .ok()
            .and_then(|settings| settings.binary);
        let args = binary
            .as_ref()
            .and_then(|binary| binary.arguments.clone())
            .unwrap_or_else(|| vec!["server".into()]);
        // Extensions cannot stat files outside their sandbox, so the project's `.venv` is the last resort
        // and a missing binary shows up as a spawn error naming that path.
        let command = binary
            .and_then(|binary| binary.path)
            .or_else(|| worktree.which(SERVER))
            .or_else(|| {
                env.iter()
                    .find(|(key, _)| key == "VIRTUAL_ENV")
                    .map(|(_, venv)| venv_binary(venv))
            })
            .unwrap_or_else(|| venv_binary(&format!("{}/.venv", worktree.root_path())));

        Ok(zed::Command { command, args, env })
    }
}

zed::register_extension!(PyJustWannaBePure);
