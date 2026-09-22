---
title: Development Tools
author: Chris Broz
date: 07/2024
styles:
    style: dracula
---

## Why

My career mission is to remove the barriers scalable science.

The tools you use are your commute to work, to get the actual job done.

- Bicycle: NoMachine, Jupyter
- Car:  DataJoint, VSCode, extensions
- Sportbike: neovim, lazygit

---

## Overview

Today, I plan to review ...

1. Setting up VSCode, including settings and extensions for ...
    - Python development (e.g., Black formatting, ruff linting)
    - Jupyter (Cell Tags, Keymap)
    - Autocompletion and Markdown (e.g., Copilot, Intellicode, wrap, spelling)
    - ssh (Remote - SSH, Remote Development, Remote Explorer)
    - git and GitHub
2. Debugging within VSCode
3. Contributing to projects on GitHub, including ...
    - forking
    - cloning
    - creating pull requests

---

## Getting this folder

1. Go to [spyglass_custom](https://github.com/LorenFrankLab/spyglass_custom)
2. Hit `Fork`, `Create fork`
3. Copy the URL: `github.com/<user>/spyglass_custom`
4. Open VSCode, go to Explorer, and hit `Clone Repository`, paste URL
5. Select a folder location

---

## VSCode Settings & Extensions

1. Skipping installations...
2. Log in to save settings across devices
3. Use the command palette (`Ctrl+Shift+P`) to select `Open Settings (JSON)`

### Settings, General

```json
{
    // GENERAL
    "editor.insertSpaces": true, // tab -> spaces
    "editor.rulers": [ 80 ], // vertical line at 80
    "editor.stickyScroll.enabled": true, // Show scope
    "files.associations": { "*.json": "jsonc" }, // Load JSON with comments
    "files.autoSave": "onFocusChange", // Save on focus change
    "files.exclude": { "**/__pycache*": true, "**/.ipynb_ch*": true, }, // Hide
    "files.trimTrailingWhitespace": true,
    "git.enabled": true,
    "workbench.editorAssociations": { "*.ipynb": "jupyter-notebook", },
}
```

My full settings [here](https://github.com/CBroz1/dotfiles/blob/main/.config/Code/User/settings.json)

---

### Extensions for Python

```bash
code --install-extension charliermarsh.ruff
code --install-extension donjayamanne.python-environment-manager
code --install-extension kevinrose.vsc-python-indent
code --install-extension ms-python.black-formatter
code --install-extension ms-python.debugpy
code --install-extension ms-python.isort
code --install-extension ms-python.pylint
code --install-extension ms-python.python
code --install-extension ms-python.vscode-pylance
# CONDA ACTIVATE ...
git clone https://github.com/CBroz1/datajoint_linter
pip install datajoint_linter # in your IDE's python environment
```

My full extension list [here](https://github.com/CBroz1/dotfiles/blob/main/vscode_extensions_install.sh)

---

### Settings for Python

```json
{
    // PYTHON
    "editor.defaultFormatter": "ms-python.black-formatter",
    "[python]": {
        "editor.formatOnSave": true,
        "editor.defaultFormatter": "ms-python.black-formatter",
        "editor.codeActionsOnSave": { "source.organizeImports": "always"},
    },
    "python.analysis.autoImportCompletions": false, // Disable auto-import
    "python.languageServer": "Pylance",
    "python.defaultInterpreterPath": "/home/cb/miniconda3/envs/YOUR_ENV",
    "pylint.path": [ "/home/cb/miniconda3/envs/YOUR_ENV/bin/pylint" ],
    "pylint.args": [
        "--load-plugins=datajoint_linter",
        "--permit-dj-filepath=y",
        "--disable=E0401,E0102,W0621,W0401,W0611,W0614"
    ],
}
```

We can look at issues/fixes in the `bad_examples` folder.

```bash
code --diff bad_examples/bad_python.py bad_examples/bad_python_fixed.py
```

---

### Extensions for Jupyter

```bash
code --install-extension ms-toolsai.jupyter
code --install-extension ms-toolsai.jupyter-keymap
code --install-extension ms-toolsai.jupyter-renderers
code --install-extension ms-toolsai.vscode-jupyter-cell-tags
```

### Settings for Jupyter

```json
{
    // NOTEBOOKS
    "jupyter.askForKernelRestart": false, // Prevent dialog box on restart
    "jupyter.widgetScriptSources": ["jsdelivr.com", "unpkg.com"], // IPyWidgets
    "notebook.output.textLineLimit": 15, // Limit output
    "notebook.lineNumbers": "on", // Number lines in cells
    "notebook.formatOnSave.enabled": true, // blackify cells
}
```

`Variables View` will show us the jupter namespace

---

## Debugging

1. Add breakpoints by clicking in the gutter
2. Run in debug mode...
    - For notebooks, use `Run` button arrow.
    - For scripts, use the debug sidebar.
3. Use the sidebar for ...
    - Viewing variables in the current frames
    - Watching variables
    - Clicking through the stack frames
4. Use the debug console to interact with the code

---

## More Extensions

### Extensions for Autocompletion and Markdown

```bash
code --install-extension github.copilot
code --install-extension github.copilot-chat
code --install-extension visualstudioexptteam.intellicode-api-usage-examples
code --install-extension visualstudioexptteam.vscodeintellicode
code --install-extension davidanson.vscode-markdownlint
code --install-extension stkb.rewrap
code --install-extension streetsidesoftware.code-spell-checker
```

---

### Settings for Autocompletion and Markdown

```json
{
    // AUTOCOMPLETION
    "editor.tabCompletion": "on", // tab over suggestions
    "github.copilot.editor.enableAutoCompletions": true, // Copilot
    "cSpell.enabled": true, // SPELL
    "cSpell.language": "en,en-US,companies,python,python-common",
    "cSpell.maxDuplicateProblems": 2, // Only mention twice
    "cSpell.spellCheckDelayMs": 500, // Wait 0.5s after save
    "cSpell.userWords": [ "datajoint", "longblob", ], // Add words
    "cSpell.enableFiletypes": [ "!json", "markdown", "yaml", "python" ],
    "cSpell.logLevel": "Warning", // Only show warnings, can turn off
    // MARKDOWN
    "[markdown]": {
    "editor.defaultFormatter": "DavidAnson.vscode-markdownlint",
        "editor.formatOnSave": true,
    },
    "editor.codeActionsOnSave": { "source.fixAll.markdownlint": "explicit" },
    "rewrap.reformat": true, // allows context-aware rewrapping
    "rewrap.wrappingColumn": 80,
}
```

We can look at issues/fixes in the `bad_examples` folder.

```bash
code --diff bad_examples/bad_markdown.md bad_examples/bad_markdown_fixed.md
```

---

### Extensions for ssh, and GitHub

```bash
code --install-extension ms-vscode-remote.remote-ssh
code --install-extension ms-vscode-remote.remote-ssh-edit
code --install-extension ms-vscode.remote-explorer
```

To SSH, we'll need to [generate a key](https://code.visualstudio.com/docs/remote/troubleshooting#_ssh-tips)

```bash
ssh-keygen -t ed25519 -b 4096 # Generate SSH key
chmod 400 ~/.ssh/id_ed25519 # Secure key permissions
export USER_AT_HOST="your-user-name-on-host@virga-01.cin.ucsf.edu"
export PUBKEYPATH="$HOME/.ssh/id_ed25519.pub"
ssh-copy-id -i "$PUBKEYPATH" -p XXXX "$USER_AT_HOST" # need port
```

---

## Contributing to GitHub

Terms: Remote, Repo, Fork, Branch

1. We already forked/cloned to a new remote
3. Branches help us maintain different versions
4. Make changes
    - Add files. This is a staging area to batch related changes.
    - Commit changes. This is a snapshot of the staging area.
    - Push changes. This moves changes to another remote.
    - Create a pull request. This suggests changes to the 'upstream' remote.

---

This is a presentation designed for use with `lookatme`:

```console
pip install lookatme
lookatme FileName.md --live
```
