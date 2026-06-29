# A broken eval file: it defines evaluate() but violates the contract by
# returning a bare float instead of a hillclimber.Eval.


def evaluate():
    return 0.42
