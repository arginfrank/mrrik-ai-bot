from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SubscribeStates(StatesGroup):
    waiting_for_txid = State()


class ApiCredentialStates(StatesGroup):
    waiting_for_credentials = State()


class SettingsStates(StatesGroup):
    waiting_for_fixed_margin = State()
