# pyjustwannabepure

This project uses its own decorators. Apply the `pyjustwannabepure` skill
(`skills/pyjustwannabepure`, linked into `.claude/skills/`) to code in `src/`,
and keep `uv run pyjustwannabepure check src` passing.

Inside the package, import the markers absolutely
(`from justwannabepure import pure, shell, side_effect`): the checker does not
recognise relative imports of them.
