"""Registro de hooks. A Task 8 registra a postagem no Slack em `on_rca_ready`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aisre.schemas import RCA

RcaHook = Callable[[int, RCA], Awaitable[None]]

on_rca_ready: list[RcaHook] = []
