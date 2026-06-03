"""contains functions not big enough to put into their own module."""


class Utilities:
    """contains functions not big enough to put into their own module."""

    @staticmethod
    def post_help() -> str:
        """:return: a string containing what commands the bot has"""
        return "The commands are: tag, git, and avatar. Each one has their own help command except for git."

    @staticmethod
    async def git() -> str:
        """:return: a url of the git repo of the source code"""
        git_url = "https://github.com/nithjino/SmurfBot"
        return f"Here is the source code: {git_url}"

    @staticmethod
    async def mock(message: str) -> str:
        """:return: message in the spongebob mocking format"""
        message = message.lower().strip()
        result = ""
        for index, character in enumerate(message):
            if character.isspace():
                result = result + " "
                continue

            if message[index - 1].isspace():
                result = result + character if index > 0 and result[-2].isupper() else result + character.upper()
            else:
                result = result + character if index > 0 and result[-1].isupper() else result + character.upper()

        return result
