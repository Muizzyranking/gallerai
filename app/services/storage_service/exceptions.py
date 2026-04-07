class StorageError(Exception):
    """Raise when storage operations fails"""


class FileTooLarge(Exception):
    """Raise when file is too large"""


class InvalidFileType(Exception):
    """Raise when file type is invalid"""
