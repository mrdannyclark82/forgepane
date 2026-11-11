"""Example trusted extension."""


def register(workspace):
    workspace.add_plugin_action("Count Selection", count_selection)


def count_selection(workspace):
    selection = workspace.selected_text()
    workspace.status.set_text(f"{len(selection)} characters · {len(selection.splitlines())} lines")
