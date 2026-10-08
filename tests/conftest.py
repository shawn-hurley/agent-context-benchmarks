"""Synchronous transport double for harness launch contract tests."""
class TransportDouble:
    def __init__(self):
        self.commands = []
        self.uploads = []
        self.downloads = []
    def capture(self, argv, workdir=None):
        self.commands.append((argv, workdir))
        return ""
    def upload(self, source, destination):
        self.uploads.append((source, destination))
    def download(self, source, destination):
        self.downloads.append((source, destination))
    def execute(self, command, **kwargs):
        self.commands.append(command)
