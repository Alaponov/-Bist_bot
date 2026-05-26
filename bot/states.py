from aiogram.fsm.state import State, StatesGroup


class LoginState(StatesGroup):
    username = State()
    password = State()