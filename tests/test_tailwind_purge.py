import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
BUILT_CSS = ROOT / "app" / "static" / "app.css"

"""`NOT_STYLED_BY_TAILWIND` holds only bare js hooks, never a tailwind utility.
If one of these names ever gains a real utility next to the querySelectorAll,
delete it from the mapping so the purge test starts checking it again."""

NOT_STYLED_BY_TAILWIND = {
    "delete-category": "js hook, document.querySelectorAll('.delete-category') in categories.html",
    "role-form": "js hook, document.querySelectorAll('.role-form') in users.html",
}

CLASS_ATTR = re.compile(r"""class=(["'])(.*?)\1""", re.DOTALL)


def _class_attrs():
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in CLASS_ATTR.finditer(text):
            yield path, match.group(2)


def _classes_in_templates():
    found = {}
    for path, value in _class_attrs():
        for name in re.sub(r"{%.*?%}", " ", value).split():
            found.setdefault(name, path.relative_to(ROOT))
    return found


def _class_selector(name):
    css_escaped = re.sub(r"([^\w-])", r"\\\1", name)
    return re.compile(r"\." + re.escape(css_escaped) + r"(?![-\w\\])")


def test_every_template_class_is_in_the_built_stylesheet():
    css = BUILT_CSS.read_text(encoding="utf-8")
    missing = {
        name: str(where)
        for name, where in _classes_in_templates().items()
        if name not in NOT_STYLED_BY_TAILWIND
        and not _class_selector(name).search(css)
    }
    assert missing == {}


def test_no_template_composes_a_class_name_from_a_variable():
    for path, value in _class_attrs():
        assert "{{" not in value, f"{path.name}: {value}"


def test_templates_do_not_load_the_tailwind_play_cdn():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in base
    assert '/static/app.css' in base
