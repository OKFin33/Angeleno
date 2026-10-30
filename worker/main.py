"""Explicit voice worker entry; no process or model is started by import."""
from worker.agent import main

if __name__ == '__main__':
    main()
