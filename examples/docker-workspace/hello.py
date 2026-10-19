def greeting(name: str) -> str:
    return f"Hello, {name}! CodeRill is running in Docker."


if __name__ == "__main__":
    print(greeting("developer"))
