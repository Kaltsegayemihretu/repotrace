class BaseStore:
    def save(self, title):
        return title


class LibraryService(BaseStore):
    def __init__(self):
        self.books = []

    def checkout(self, title):
        return self.save(title)
