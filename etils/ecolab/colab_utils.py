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

"""Colab utils."""

from __future__ import annotations

import contextlib
import html
import json as json_std
import signal
import threading
import typing
from typing import Iterator, Iterable, TypeVar
import uuid

import IPython.display

if typing.TYPE_CHECKING:
  JsonValue = str | float | int | bool | None
  Json = JsonValue | dict[JsonValue, 'Json'] | list['Json']

_T = TypeVar('_T')


@contextlib.contextmanager
def collapse(name: str = '', *, expanded: bool = False) -> Iterator[None]:
  """Capture all outputs and display it in a collapsible block.

  Args:
    name: Name of the collapsible section.
    expanded: If `True`, the section is expanded by default.

  Yields:
    None
  """
  import ipywidgets  # pylint: disable=g-import-not-at-top

  out = ipywidgets.Output()
  accordion = ipywidgets.Accordion(children=[out])
  accordion.set_title(0, name)
  if expanded:
    accordion.selected_index = 0
  else:
    accordion.selected_index = None
  IPython.display.display(accordion)
  with out:
    try:
      yield
    except Exception as e:
      # ipywidgets.Output erase exceptions, so we save it and reraise it after
      # the scope.
      exc = e
      raise
    else:
      exc = None
  if exc is not None:
    raise exc  # pylint: disable=g-doc-exception


def json(value: Json) -> None:
  """Display the Json `dict` / `list` interactivelly (with collapsible elems).

  Examples:

  ```python
  ecolab.json({'a': [1, 2, 3], 'b': {'x': True, 'y': False}})
  ```

  The dict keys and list indices can be filtered from the display field using
  regex (e.g. `a.[0-9]` in the above example).

  Args:
    value: Json `dict` or `list` to inspect.
  """
  # Unique id to make sure multiple Json display do not interact with each other
  id_ = uuid.uuid1().hex

  # There are a lot of alternative to `alenaksu/json-viewer`.
  # Likely the most popular one is `react-json-view`. However, this one
  # display preview for collapsible elements, which is nice (and not present
  # in other alternatives).
  # https://github.com/mac-s-g/react-json-view/issues/237

  css_content = """
  json-viewer {
    padding:1px 1.5em 1px 1.5em;
    --background-color: #f7f7f7;
    --property-color: #087db2;
    --string-color: #a31515;
    --number-color: #008000;
    --boolean-color: #0000ff;
    --null-color: #af00db;
    --preview-color: #888888;
  }
  json-viewer::part(key) {
    margin-right: 0.5em;
  }
  html[theme=dark] json-viewer {
    --background-color: #2c2c2c;
    --property-color: #6fb3d2;
    --string-color: #ce9178;
    --number-color: #b5cea8;
    --boolean-color: #569cd6;
    --null-color: #c586c0;
    --preview-color: #888888;
  }
  .ecolab-json button {
      background-color: var(--colab-highlighted-surface-color);
      color: var(--colab-primary-text-color);
      border-width: 0;
  }
  .ecolab-json input {
      border-color: var(--colab-highlighted-surface-color);
  }
  """

  html_content = html.escape(json_std.dumps(value))
  html_content = f"""
  <script src="https://unpkg.com/@alenaksu/json-viewer@2.0.0/dist/json-viewer.bundle.js"></script>
  <script>
    const viewer{id_} = document.querySelector('#json{id_}');
    viewer{id_}.expandAll();
  </script>
  <style>
  {css_content}
  </style>
  <div class="ecolab-json">
    <button onclick="viewer{id_}.expandAll();">Expand All</button>
    <button onclick="viewer{id_}.collapseAll();">Collapse All</button>
    <input placeholder="Filter Regex" onkeyup="viewer{id_}.filter(RegExp(this.value, 'i'));"></input>
    <json-viewer id="json{id_}">{html_content}</json-viewer>
  </div>
  """
  IPython.display.display(IPython.display.HTML(html_content))


def interruptible(inner: Iterable[_T] | Iterator[_T]) -> Iterator[_T]:
  """Catch KeyboardInterrupts to end the loop without raising an Exception.

  While this iterator is running, the first SIGINT (e.g. from interrupting
  the colab runtime, or pressing Ctrl+C ) will not raise an exception but
  instead end the loop after the current iteration.
  A second SIGINT will raise a KeyboardInterrupt Exception as usual.

  Examples:
  ```python
  for i in interruptible(range(10)):
    print(i)
    time.sleep(3)
  ```

  Args:
    inner: arbitrary iterable or iterator

  Yields:
    elements from inner
  """
  interrupted = threading.Event()

  def handler(unused_signum, unused_frame):
    if interrupted.is_set():
      raise KeyboardInterrupt
    interrupted.set()

  previous_handler = signal.signal(signal.SIGINT, handler)

  try:
    for i in inner:
      yield i
      if interrupted.is_set():
        return
  finally:
    signal.signal(signal.SIGINT, previous_handler)
