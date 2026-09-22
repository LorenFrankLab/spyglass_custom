"""Syntactically valid Python script with some issues fixed by Ruff auto-fix"""
# Incorrect import order
# Unused imports
from math import *


# Function name should be lowercase with underscores
def MyFunction():
    # Indentation and spacing issues
    print( "Hello World" )
    x=10
    y =  20
    # Unused variable
    unused_variable = 0

    if (x == 10):
        print('x is ten')
        # Unnecessary semicolon
        y += 1

    # Undefined variable
    print(z)

    # Useless pass statement
    pass

# Long line
a = "This is a very long string that should probably be split into multiple lines to adhere to best practices but isn't"

# Variable names should be lowercase with underscores
CamelCaseVariable = "This should be snake_case"

# Reimported module


# Too many arguments in a function
def too_many_arguments(arg1, arg2, arg3, arg4, arg5, arg6, arg7):
    return arg1 arg2 + arg3 + arg4 + arg5 + arg6 + arg7

# Trailing whitespace
def trailing_whitespace():
    return "This line has trailing whitespace"

# Multiple statements on one line
a = 1; b = 2; c = a + b

# Bad indentation and inconsistent use of tabs and spaces
def bad_indentation():
    if True:
       print("Bad indentation")
    else:
      print("Worse indentation")

# Use of eval
user_input = "print('This is dangerous')"
eval(user_input)

# Use of assert statement outside of testing
assert 1 == 1, "This should not be here"

# No module docstring
def another_function():
    """This function does nothing"""
    pass
    """This function does nothing"""
    pass
