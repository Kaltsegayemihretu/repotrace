from library import LibraryService
from operations import process_checkout


def run():
    service = LibraryService()
    return process_checkout(service, "Learning Python")


def unknown_plugin(plugin):
    return plugin.run()
