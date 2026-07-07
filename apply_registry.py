import os, glob, re

long_dir = r"C:\Users\YSR_MONSTER\.antigravity\Borsa\strategies\crypto\long"
short_dir = r"C:\Users\YSR_MONSTER\.antigravity\Borsa\strategies\crypto\short"

def process_files(directory, decorator_str):
    for filepath in glob.glob(os.path.join(directory, "*.py")):
        if filepath.endswith("__init__.py"):
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if "StrategyRegistry" not in content:
            # Add import after BaseStrategy import
            content = re.sub(
                r'from \.\.\.base import BaseStrategy',
                r'from ...base import BaseStrategy, StrategyRegistry',
                content
            )
            content = re.sub(
                r'from \.\.base import BaseStrategy',
                r'from ..base import BaseStrategy, StrategyRegistry',
                content
            )

        # Apply decorator if not already there
        if decorator_str not in content:
            content = re.sub(
                r'class ([A-Za-z0-9_]+Strategy)\(BaseStrategy\):',
                f'{decorator_str}\nclass \\1(BaseStrategy):',
                content
            )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

process_files(long_dir, "@StrategyRegistry.register_long")
process_files(short_dir, "@StrategyRegistry.register_short")
print("Decorators applied.")
