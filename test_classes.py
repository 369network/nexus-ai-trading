import sys
sys.path.insert(0, '/app')

checks = [
    ('src.agents.bear_researcher',       'BearResearcher'),
    ('src.agents.bull_researcher',       'BullResearcher'),
    ('src.agents.technical_analyst',     'TechnicalAnalyst'),
    ('src.agents.sentiment_analyst',     'SentimentAnalyst'),
    ('src.agents.fundamental_analyst',   'FundamentalAnalyst'),
    ('src.agents.portfolio_manager',     'PortfolioManager'),
    ('src.agents.risk_manager',          'RiskManagerAgent'),
    ('src.strategies.crypto.momentum',   'MomentumStrategy'),
    ('src.strategies.crypto.mean_reversion', 'MeanReversionStrategy'),
]

for mod, expected in checks:
    try:
        m = __import__(mod, fromlist=['x'])
        classes = [n for n in dir(m) if n[0].isupper() and not n.startswith('__')]
        has = expected in classes
        status = 'OK  ' if has else 'MISS'
        print(f'{status} {mod}')
        if not has:
            print(f'       expected={expected!r}, found={classes}')
    except Exception as e:
        print(f'ERR  {mod}: {e}')
