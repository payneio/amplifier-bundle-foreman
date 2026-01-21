"""Basic tests for calculator module."""

import pytest
from src.calculator import add, subtract, multiply, divide, calculate


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(1, 5) == -4


def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(-2, 3) == -6


def test_divide():
    assert divide(10, 2) == 5
    assert divide(7, 2) == 3.5


def test_divide_by_zero():
    # Current behavior returns None - not ideal
    assert divide(5, 0) is None


def test_calculate():
    assert calculate("add", 2, 3) == 5
    assert calculate("multiply", 4, 5) == 20


def test_calculate_invalid():
    # Current behavior silently returns None
    assert calculate("invalid", 1, 2) is None
