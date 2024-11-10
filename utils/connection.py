# utils/connection.py
import MetaTrader5 as mt5

def initialize_mt5():
    """Initialize connection to MetaTrader 5."""
    if not mt5.initialize():
        print("Failed to initialize MT5 connection")
        return False
    print("MT5 initialized successfully")
    return True

def shutdown_mt5():
    """Shutdown the connection to MetaTrader 5."""
    mt5.shutdown()
    print("MT5 connection closed.")
