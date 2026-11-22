"""The same name can mean different things in different functions."""


def save():
    pass


def first():
    save()  # Connects to the module's save function.


def second(save):
    save()  # Unknown: save is supplied by whoever calls second.


def outer():
    def helper():
        pass

    def inner():
        helper()  # Connects to outer.helper.

    inner()  # Connects to outer.inner.
