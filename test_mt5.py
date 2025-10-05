import MetaTrader5 as mt5

print("Initializing MT5...")
mt5.initialize()
print("MT5 initialized:", mt5.initialize())

symbol = "USDJPY"  # Use the exact symbol name as in Market Watch
result = mt5.symbol_select(symbol, True)
print(f"Symbol select result for {symbol}:", result)
print("Last error:", mt5.last_error())
