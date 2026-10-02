from .bridge import Hermes


def create_instance(c_instance):
    return Hermes(c_instance)
