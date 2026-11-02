"""Application assets and operator-owned runtime paths."""
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_SOURCE = _PACKAGE.parents[1]
SOURCE_ROOT = _SOURCE if (_SOURCE / 'pyproject.toml').is_file() and (_SOURCE / 'src/angeleno') == _PACKAGE else None
# Relative operator paths belong to the launch directory, never site-packages.
# Running from the checkout preserves the existing private/ development default.
ROOT = Path.cwd().resolve()


def resource_path(relative):
    """Locate shipped read-only assets, independent of the working directory."""
    base = SOURCE_ROOT if SOURCE_ROOT is not None else _PACKAGE / '_resources'
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('Resource path must remain in the application assets')
    return path


def resolve_path(value, *, root=ROOT):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(root) / path).resolve()


def resource_or_path(value, *, root=ROOT):
    """Use an explicit local file, or one of the shipped default asset paths."""
    path = resolve_path(value, root=root)
    if path.exists() or Path(value).is_absolute():
        return path
    return resource_path(value)


def data_directory(env, *, root=ROOT):
    value = env.get('ANGELENO_DATA_DIR', 'private/state')
    if not str(value).strip():
        raise ValueError('ANGELENO_DATA_DIR must select a data directory')
    return resolve_path(value, root=root)


def private_roots(env, *, root=ROOT):
    return (Path(root).resolve() / 'private', data_directory(env, root=root))


def private_file(value, env, *, root=ROOT):
    """Confine diagnostic/export files to configured operator storage, without links."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    for base in private_roots(env, root=root):
        if base.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
            continue
        # Check the lexical path too: resolving must not hide symlink traversal.
        for candidate in (path, *path.parents):
            if candidate.is_symlink():
                raise ValueError('Private files must not follow symlinks')
            if candidate.resolve() == base.resolve():
                return path.resolve()
    raise ValueError('Private file must be inside ANGELENO_DATA_DIR or local private/')
