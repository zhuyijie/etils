# Copyright 2023 The etils Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""GPath wrapper around the gfile API."""

from __future__ import annotations

import ntpath
import os
import pathlib
import posixpath
import types
import typing
from typing import Any, ClassVar, Iterator, Optional, Type, TypeVar, Union

from etils import epy
from etils.epath import abstract_path
from etils.epath import backend as backend_lib
from etils.epath import stat_utils
from etils.epath.typing import PathLike

_P = TypeVar('_P')

URI_PREFIXES = ('gs://', 's3://', 'hdfs://')
_URI_SCHEMES = frozenset(('gs', 's3', 'hdfs'))

_URI_MAP_ROOT = {
    'gs://': '/gs/',
    's3://': '/s3/',
    'hdfs://': '/hdfs/',
}

_PREFIX_TO_BACKEND = {
    'gs': backend_lib.tf_backend,
    's3': backend_lib.tf_backend,
    'hdfs': backend_lib.tf_backend,
    None: backend_lib.os_backend,
}
_GCS_BACKENDS = frozenset(
    {
        backend_lib.tf_backend,
    }
)

# Available modes (from tensorflow/python/lib/io/file_io.py;l=55)
# Also exclude `+` as broken in gfile
_OPEN_MODES = ('r', 'w', 'a')


class _GPath(abstract_path.Path):
  """Pathlib like api with gs://, s3:// support."""

  # `_PATH` is `posixpath` or `ntpath`.
  # Use explicit `join()` rather than `super().joinpath()` to avoid infinite
  # recursion.
  # Do not use `os.path`, so `PosixGPath('gs://abc')` works on windows.
  _PATH: ClassVar[types.ModuleType]

  def __new__(cls: Type[_P], *parts: PathLike) -> _P:
    full_path = '/'.join(os.fspath(p) for p in parts)
    if full_path.startswith(URI_PREFIXES):
      prefix, _ = full_path.split('://', maxsplit=1)
      prefix = f'{prefix}://'
      new_prefix = _URI_MAP_ROOT[prefix]
      return super().__new__(cls, full_path.replace(prefix, new_prefix, 1))
    else:
      return super().__new__(cls, *parts)

  def _new(self: _P, *parts: PathLike) -> _P:
    """Create a new `Path` child of same type."""
    return type(self)(*parts)

  # Could try to use `cached_property` when beam is compatible (currently
  # raise mutable input error when used with beam).
  @property
  def _uri_scheme(self) -> Optional[str]:
    if (
        len(self.parts) >= 2
        and self.parts[0] == '/'
        and self.parts[1] in _URI_SCHEMES
    ):
      return self.parts[1]
    else:
      return None

  @property
  def _backend(self) -> backend_lib.Backend:
    try:
      return _PREFIX_TO_BACKEND[self._uri_scheme]
    except KeyError:
      supported = ', '.join(f'`{k}://`' for k in _PREFIX_TO_BACKEND)
      raise NotImplementedError(
          f'Unsuported scheme `{self._uri_scheme}://` (supported: {supported})'
      ) from None

  @property
  def _path_str(self) -> str:
    """Returns the `__fspath__` string representation."""
    uri_scheme = self._uri_scheme
    if uri_scheme:  # pylint: disable=using-constant-test
      return self._PATH.join(f'{uri_scheme}://', *self.parts[2:])
    else:
      return self._PATH.join(*self.parts) if self.parts else '.'

  def __fspath__(self) -> str:
    return self._path_str

  def __str__(self) -> str:  # pylint: disable=invalid-str-returned
    return self._path_str

  def __repr__(self) -> str:
    return f'{type(self).__name__}({self._path_str!r})'

  def as_uri(self) -> str:
    if self._uri_scheme:  # s3://,...
      return self._path_str
    return super().as_uri()

  def exists(self) -> bool:
    """Returns True if self exists."""
    return self._backend.exists(self._path_str)

  def is_dir(self) -> bool:
    """Returns True if self is a directory."""
    return self._backend.isdir(self._path_str)

  def iterdir(self: _P) -> Iterator[_P]:
    """Iterates over the directory."""
    for f in self._backend.listdir(self._path_str):
      yield self._new(self, f)

  def expanduser(self: _P) -> _P:
    """Returns a new path with expanded `~` and `~user` constructs."""
    return self._new(self._PATH.expanduser(self._path_str))

  def resolve(self: _P, strict: bool = False) -> _P:
    """Returns the abolute path."""
    # TODO(epot): In pathlib, `resolve` also resolve the symlinks
    return self._new(self._PATH.abspath(self._path_str))

  def glob(self: _P, pattern: str) -> Iterator[_P]:
    """Yielding all matching files (of any kind)."""
    pattern = self._PATH.join(self._path_str, pattern)

    if '**' in pattern:
      raise NotImplementedError(
          'Recursive `**` pattern not supported as this could trigger '
          'thoushands of RPC requests on GCS. Use `*` instead. '
          f'Got: {pattern!r}'
      )

    for f in self._backend.glob(pattern):
      yield self._new(f)

  def mkdir(
      self,
      mode: int = 0o777,
      parents: bool = False,
      exist_ok: bool = False,
  ) -> None:
    """Create a new directory at this given path."""
    if mode != 0o777:
      # tf.io.gfile do not support setting `mode=`
      raise NotImplementedError(
          'mkdir with custom `mode=` not supported. Please open an issue.'
      )

    if parents:
      self._backend.makedirs(self._path_str, exist_ok=exist_ok)
    else:
      self._backend.mkdir(self._path_str, exist_ok=exist_ok)

  def rmdir(self) -> None:
    """Remove the empty directory."""
    if not self.is_dir():
      raise NotADirectoryError(f'{self._path_str} is not a directory.')
    if list(self.iterdir()):
      raise ValueError(f'Directory {self._path_str} is not empty')
    self._backend.rmtree(self._path_str)

  def rmtree(self) -> None:
    """Remove the directory."""
    self._backend.rmtree(self._path_str)

  def unlink(self, missing_ok: bool = False) -> None:
    """Remove this file or symbolic link."""
    try:
      self._backend.remove(self._path_str)
    except FileNotFoundError:
      if missing_ok:
        pass
      else:
        raise

  def open(  # pytype: disable=signature-mismatch  # overriding-parameter-count-checks
      self,
      mode: str = 'r',
      *,
      encoding: Optional[str] = None,
      errors: Optional[str] = None,
      **kwargs: Any,
  ) -> typing.IO[Union[str, bytes]]:
    """Opens the file."""
    if errors:
      raise NotImplementedError('`errors=` not supported in `open()`.')
    if encoding and not encoding.lower().startswith(('utf8', 'utf-8')):
      raise ValueError(f'Only UTF-8 encoding supported. Not: {encoding}')
    # TODO(epot): Could support `x` mode

    mode_without_b = mode.replace('b', '')
    if mode_without_b not in _OPEN_MODES:
      raise ValueError(f'mode={mode_without_b!r} is not one of {_OPEN_MODES}')
    if kwargs:
      raise NotImplementedError(
          f'kwargs {list(kwargs)}` not supported in `open()`.'
      )
    gfile = self._backend.open(self._path_str, mode)
    gfile = typing.cast(typing.IO[Union[str, bytes]], gfile)
    return gfile

  def rename(self: _P, target: PathLike) -> _P:
    """Rename file or directory to the given target."""
    # Note: Issue if WindowsPath and target is gs://. Rather than using `_new`,
    # `GPath.__new__` should dynamically return either `PosixGPath` or
    # `WindowsPath`, similarly to `pathlib.Path`.
    target = self._new(target)
    backend = _get_backend(self, target)
    backend.rename(self._path_str, os.fspath(target))
    return target

  def replace(self: _P, target: PathLike) -> _P:
    """Replace file or directory to the given target."""
    target = self._new(target)
    backend = _get_backend(self, target)
    backend.replace(self._path_str, os.fspath(target))
    return target

  def copy(self: _P, dst: PathLike, overwrite: bool = False) -> _P:
    """Copy file or directory to the given target."""
    # Could add a recursive=True mode
    dst = self._new(dst)
    backend = _get_backend(self, dst)
    backend.copy(self._path_str, os.fspath(dst), overwrite=overwrite)
    return dst

  def stat(self) -> stat_utils.StatResult:
    """Returns metadata for the file/directory."""
    return self._backend.stat(self._path_str)


def _get_backend(p0: _GPath, p1: _GPath) -> backend_lib.Backend:
  """When composing with another backend, GCS win.

  To allow `Path('.').replace('gs://')`

  Args:
    p0: Path to compare
    p1: Path to compare

  Returns:
    GCS backend if one of the 2 path is GCS, else p0 backend.
  """
  # pylint: disable=protected-access
  if p0._backend in _GCS_BACKENDS:
    return p0._backend
  elif p1._backend in _GCS_BACKENDS:
    return p1._backend
  else:
    return p0._backend
  # pylint: enable=protected-access


class PosixGPath(_GPath):
  """Pathlib like api with gs://, s3:// support."""

  _PATH = posixpath


class WindowsGPath(pathlib.PureWindowsPath, _GPath):
  """Pathlib like api with gs://, s3:// support."""

  _PATH = ntpath
