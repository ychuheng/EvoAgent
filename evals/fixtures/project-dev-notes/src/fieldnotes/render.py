"""Format stored notes for terminal output."""


def render_notes(notes: list[dict[str, str]]) -> str:
    return "\n".join(f"{note['title']}: {note['body']}" for note in notes)
