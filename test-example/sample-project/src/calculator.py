"""Simple calculator module - intentionally has room for improvement.

This module is used as a test target for the foreman bundle integration test.
The foreman will be asked to refactor and improve this code.
"""


def add(a, b):
    """Add two numbers."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b


def multiply(a, b):
    """Multiply two numbers."""
    return a * b


def divide(a, b):
    """Divide a by b."""
    if b == 0:
        return None  # Not great error handling
    return a / b


def calculate(operation, a, b):
    """Perform calculation based on operation string.
    
    This function has issues:
    - No input validation
    - Uses string comparison instead of enum
    - No type hints
    - Poor error handling
    """
    if operation == "add":
        return add(a, b)
    elif operation == "subtract":
        return subtract(a, b)
    elif operation == "multiply":
        return multiply(a, b)
    elif operation == "divide":
        return divide(a, b)
    else:
        return None  # Silent failure


# Global state - not ideal
history = []


def calculate_and_store(operation, a, b):
    """Calculate and store in history."""
    result = calculate(operation, a, b)
    history.append({"op": operation, "a": a, "b": b, "result": result})
    return result


def get_history():
    """Get calculation history."""
    return history


def clear_history():
    """Clear calculation history."""
    global history
    history = []
