#!/usr/bin/env python3
"""
Simple test to debug the script execution issue
"""
import sys

print("Script is executing!")
print(f"Python version: {sys.version}")
print(f"Arguments: {sys.argv}")

if __name__ == "__main__":
    print("Main block is executing!")
