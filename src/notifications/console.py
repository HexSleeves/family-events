class ConsoleNotifier:
    async def send(self, message: str) -> bool:
        print("\n" + "=" * 50)
        print(message)
        print("=" * 50 + "\n")
        return True
