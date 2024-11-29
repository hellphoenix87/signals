# Trading Signal Generation System

This project is a trading signal generation system that calculates key technical indicators (SMA, RSI, MACD) for financial data (e.g., Forex, Stocks) and combines these indicators to produce strong buy, sell, or hold signals. The system is designed for use in automated trading bots or analysis systems.

## Features

- Fetch financial market data for a specific symbol and timeframe.
- Calculate key technical indicators:
  - **Simple Moving Average (SMA)**
  - **Relative Strength Index (RSI)**
  - **Moving Average Convergence Divergence (MACD)**
- Combine individual signals from SMA, RSI, and MACD to generate a final strong signal.
- Handle errors gracefully and log detailed information for troubleshooting.
- Supports retry logic in case of data fetch failure.

## Requirements

- Python 3.x
- Libraries:
  - `numpy`
  - `asyncio`
  - `MetaTrader5` (MT5)
  - `logging`

Install dependencies using pip:

```bash
pip install numpy MetaTrader5
