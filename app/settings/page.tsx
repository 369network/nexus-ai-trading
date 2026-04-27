'use client';

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {
  Settings,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  RefreshCw,
  StopCircle,
  Wifi,
  WifiOff,
  FlaskConical,
  Sliders,
  Shield,
} from 'lucide-react';
import { useNexusStore } from '@/lib/store';
import { cn, formatTimeAgo } from '@/lib/utils';

interface FeatureFlag {
  key: string;
  label: string;
  description: string;
  value: boolean;
  category: string;
}

const FEATURE_FLAGS: FeatureFlag[] = [
  { key: 'auto_trading', label: 'Auto Trading', description: 'Allow system to place trades automatically', value: true, category: 'Trading' },
  { key: 'multi_market', label: 'Multi-Market Mode', description: 'Trade across all 5 markets simultaneously', value: true, category: 'Trading' },
  { key: 'ai_debate', label: 'AI Agent Debate', description: 'Enable multi-agent debate before signaling', value: true, category: 'AI' },
  { key: 'brier_weighting', label: 'Brier Score Weighting', description: 'Weight agent votes by historical Brier scores', value: true, category: 'AI' },
  { key: 'funding_signals', label: 'Funding Rate Signals', description: 'Use crypto funding rates as signal input', value: true, category: 'Signals' },
  { key: 'whale_alerts', label: 'Whale Alert Filter', description: 'Avoid trading against large institutional flows', value: false, category: 'Signals' },
  { key: 'cot_signals', label: 'COT Data Signals', description: 'Use CFTC COT data for forex positioning', value: true, category: 'Signals' },
  { key: 'sentiment_news', label: 'News Sentiment', description: 'Include NLP news analysis in sentiment scoring', value: true, category: 'Signals' },
  { key: 'trailing_stop', label: 'Dynamic Trailing Stop', description: 'Use ATR-based trailing stops', value: true, category: 'Risk' },
  { key: 'correlation_filter', label: 'Correlation Filter', description: 'Reduce sizing when positions are correlated', value: true, category: 'Risk' },
  { key: 'regime_detection', label: 'Market Regime Detection', description: 'Adapt strategy to trending/ranging markets', value: false, category: 'AI' },
  { key: 'paper_alerts', label: 'Paper Mode Alerts', description: 'Send alerts even in paper trading mode', value: true, category: 'Alerts' },
];

const LLM_WEIGHTS: { agent: string; weight: number; optimal: number }[] = [
  { agent: 'Bull', weight: 0.72, optimal: 0.68 },
  { agent: 'Bear', weight: 0.65, optimal: 0.71 },
  { agent: 'Fundamental', weight: 0.81, optimal: 0.78 },
  { agent: 'Technical', weight: 0.88, optimal: 0.85 },
  { agent: 'Sentiment', weight: 0.62, optimal: 0.65 },
];

const RISK_LIMITS = [
  { key: 'crypto_max_pct', label: 'Crypto Max Exposure %', value: 30, min: 5, max: 50 },
  { key: 'forex_max_pct', label: 'Forex Max Exposure %', value: 20, min: 5, max: 40 },
  { key: 'commodities_max_pct', label: 'Commodities Max %', value: 15, min: 5, max: 30 },
  { key: 'indian_stocks_max_pct', label: 'Indian Stocks Max %', value: 15, min: 5, max: 30 },
  { key: 'us_stocks_max_pct', label: 'US Stocks Max %', value: 10, min: 5, max: 25 },
  { key: 'daily_stop_pct', label: 'Daily Stop Loss %', value: 3, min: 1, max: 10 },
  { key: 'max_drawdown_pct', label: 'Max Drawdown Halt %', value: 15, min: 5, max: 25 },
  { key: 'max_position_pct', label: 'Max Position Size %', value: 10, min: 1, max: 20 },
];

const CIRCUIT_THRESHOLDS = [
  { key: 'daily_loss_pct', label: 'Daily Loss Limit %', value: 3.0, step: 0.5 },
  { key: 'drawdown_pct', label: 'Max Drawdown %', value: 15.0, step: 1.0 },
  { key: 'concentration_pct', label: 'Position Concentration %', value: 10.0, step: 1.0 },
  { key: 'correlation_max', label: 'Correlation Limit', value: 0.70, step: 0.05 },
  { key: 'vix_threshold', label: 'VIX Spike Threshold', value: 35.0, step: 1.0 },
];

const API_CONNECTIONS = [
  { name: 'Binance', type: 'Exchange', status: 'connected', ping: 42, lastPing: new Date().toISOString() },
  { name: 'Coinbase', type: 'Exchange', status: 'connected', ping: 87, lastPing: new Date().toISOString() },
  { name: 'Interactive Brokers', type: 'Broker', status: 'connected', ping: 124, lastPing: new Date().toISOString() },
  { name: 'Zerodha Kite', type: 'Broker', status: 'connected', ping: 211, lastPing: new Date().toISOString() },
  { name: 'Alpha Vantage', type: 'Data', status: 'error', ping: 0, lastPing: new Date(Date.now() - 300000).toISOString() },
  { name: 'NewsAPI', type: 'Data', status: 'connected', ping: 178, lastPing: new Date().toISOString() },
  { name: 'Glassnode', type: 'Data', status: 'connected', ping: 234, lastPing: new Date().toISOString() },
];

function ToggleSwitch({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={cn(
        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
        'focus:outline-none focus:ring-2 focus:ring-nexus-blue focus:ring-offset-2 focus:ring-offset-background',
        checked ? 'bg-nexus-green' : 'bg-gray-600',
        disabled && 'opacity-40 cursor-not-allowed'
      )}
    >
      <span
        className={cn(
          'inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform shadow-sm',
          checked ? 'translate-x-4.5' : 'translate-x-0.5'
        )}
        style={{ transform: checked ? 'translateX(20px)' : 'translateX(2px)' }}
      />
    </button>
  );
}

function EmergencyStopDialog() {
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [input, setInput] = useState('');

  const handleStop = () => {
    if (input === 'STOP') {
      // In production: call emergency stop API
      console.log('EMERGENCY STOP TRIGGERED');
      setOpen(false);
      setInput('');
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button className="w-full py-3 px-4 bg-nexus-red/10 border border-nexus-red/30 text-nexus-red font-bold rounded-lg hover:bg-nexus-red/20 transition-colors flex items-center justify-center gap-2">
          <StopCircle size={18} />
          EMERGENCY STOP ALL TRADING
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-card border border-nexus-red/40 rounded-xl p-6 w-96 z-50">
          <Dialog.Title className="text-nexus-red font-bold text-lg flex items-center gap-2 mb-2">
            <AlertTriangle size={20} />
            Emergency Stop
          </Dialog.Title>
          <Dialog.Description className="text-sm text-gray-300 mb-4">
            This will immediately close all open positions and halt all trading activity. This action cannot be undone.
          </Dialog.Description>
          <div className="bg-nexus-red/5 border border-nexus-red/20 rounded-lg p-3 mb-4">
            <p className="text-xs text-nexus-red font-medium">This will:</p>
            <ul className="text-xs text-gray-300 mt-1 space-y-1">
              <li>• Close all {4} open positions at market price</li>
              <li>• Cancel all pending orders</li>
              <li>• Halt the trading engine</li>
              <li>• Trigger all circuit breakers</li>
            </ul>
          </div>
          <div className="mb-4">
            <label className="text-xs text-muted block mb-1">Type STOP to confirm:</label>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-nexus-red"
              placeholder="STOP"
            />
          </div>
          <div className="flex gap-3">
            <Dialog.Close asChild>
              <button className="flex-1 py-2 px-4 bg-border text-white rounded-lg text-sm hover:bg-border-bright transition-colors">
                Cancel
              </button>
            </Dialog.Close>
            <button
              onClick={handleStop}
              disabled={input !== 'STOP'}
              className="flex-1 py-2 px-4 bg-nexus-red text-white font-bold rounded-lg text-sm hover:bg-red-600 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              STOP ALL
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PaperModeToggle() {
  const { systemStatus, updateSystemStatus } = useNexusStore();
  const [open, setOpen] = useState(false);

  const handleToggle = () => {
    if (systemStatus.paper_mode) {
      // Going live - need confirmation
      setOpen(true);
    } else {
      // Going to paper - safe to do immediately
      updateSystemStatus({ paper_mode: true });
    }
  };

  const confirmGoLive = () => {
    updateSystemStatus({ paper_mode: false });
    setOpen(false);
  };

  return (
    <div className={cn(
      'p-4 rounded-xl border-2 transition-all',
      systemStatus.paper_mode
        ? 'border-nexus-yellow/40 bg-nexus-yellow/5'
        : 'border-nexus-green/40 bg-nexus-green/5'
    )}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn('w-10 h-10 rounded-full flex items-center justify-center', systemStatus.paper_mode ? 'bg-nexus-yellow/10' : 'bg-nexus-green/10')}>
            <FlaskConical size={20} className={systemStatus.paper_mode ? 'text-nexus-yellow' : 'text-nexus-green'} />
          </div>
          <div>
            <div className={cn('font-bold text-base', systemStatus.paper_mode ? 'text-nexus-yellow' : 'text-nexus-green')}>
              {systemStatus.paper_mode ? 'PAPER TRADING MODE' : 'LIVE TRADING MODE'}
            </div>
            <div className="text-xs text-muted">
              {systemStatus.paper_mode
                ? 'All trades are simulated. No real money at risk.'
                : 'CAUTION: Real money is at risk. Trades execute on live markets.'}
            </div>
          </div>
        </div>
        <ToggleSwitch
          checked={!systemStatus.paper_mode}
          onChange={handleToggle}
        />
      </div>

      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50" />
          <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-card border border-nexus-yellow/40 rounded-xl p-6 w-96 z-50">
            <Dialog.Title className="text-nexus-yellow font-bold text-lg flex items-center gap-2 mb-2">
              <AlertTriangle size={20} />
              Switch to Live Trading?
            </Dialog.Title>
            <Dialog.Description className="text-sm text-gray-300 mb-4">
              You are about to switch from paper trading to live trading. Real money will be used for all trades.
            </Dialog.Description>
            <div className="bg-nexus-yellow/5 border border-nexus-yellow/20 rounded-lg p-3 mb-4 text-xs text-gray-300 space-y-1">
              <p>• Verify all API keys are configured</p>
              <p>• Confirm risk limits are set appropriately</p>
              <p>• Ensure sufficient capital is allocated</p>
              <p>• All circuit breakers will reset</p>
            </div>
            <div className="flex gap-3">
              <Dialog.Close asChild>
                <button className="flex-1 py-2 px-4 bg-border text-white rounded-lg text-sm hover:bg-border-bright transition-colors">
                  Stay Paper
                </button>
              </Dialog.Close>
              <button
                onClick={confirmGoLive}
                className="flex-1 py-2 px-4 bg-nexus-yellow text-black font-bold rounded-lg text-sm hover:bg-yellow-400 transition-colors"
              >
                GO LIVE
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

export default function SettingsPage() {
  const [flags, setFlags] = useState(FEATURE_FLAGS);
  const [weights, setWeights] = useState(LLM_WEIGHTS);
  const [limits, setLimits] = useState(RISK_LIMITS);
  const [thresholds, setThresholds] = useState(CIRCUIT_THRESHOLDS);

  const toggleFlag = (key: string) => {
    setFlags((prev) => prev.map((f) => f.key === key ? { ...f, value: !f.value } : f));
  };

  const updateWeight = (agent: string, weight: number) => {
    setWeights((prev) => prev.map((w) => w.agent === agent ? { ...w, weight } : w));
  };

  const resetWeightsToOptimal = () => {
    setWeights((prev) => prev.map((w) => ({ ...w, weight: w.optimal })));
  };

  const updateLimit = (key: string, value: number) => {
    setLimits((prev) => prev.map((l) => l.key === key ? { ...l, value } : l));
  };

  const updateThreshold = (key: string, value: number) => {
    setThresholds((prev) => prev.map((t) => t.key === key ? { ...t, value } : t));
  };

  const flagsByCategory = flags.reduce((acc, f) => {
    if (!acc[f.category]) acc[f.category] = [];
    acc[f.category].push(f);
    return acc;
  }, {} as Record<string, FeatureFlag[]>);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Paper Mode Toggle - most prominent */}
      <PaperModeToggle />

      <div className="grid grid-cols-2 gap-6">
        {/* Feature Flags */}
        <div className="nexus-card p-5">
          <div className="flex items-center gap-2 mb-5">
            <Settings size={16} className="text-nexus-blue" />
            <h2 className="font-semibold text-white">Feature Flags</h2>
          </div>

          <div className="space-y-5">
            {Object.entries(flagsByCategory).map(([category, categoryFlags]) => (
              <div key={category}>
                <div className="text-xs text-muted uppercase tracking-wider mb-2">{category}</div>
                <div className="space-y-3">
                  {categoryFlags.map((flag) => (
                    <div key={flag.key} className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-white">{flag.label}</div>
                        <div className="text-xs text-muted">{flag.description}</div>
                      </div>
                      <ToggleSwitch
                        checked={flag.value}
                        onChange={() => toggleFlag(flag.key)}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* LLM Weights */}
        <div className="space-y-4">
          <div className="nexus-card p-5">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-2">
                <Sliders size={16} className="text-nexus-purple" />
                <h2 className="font-semibold text-white">LLM Agent Weights</h2>
              </div>
              <button
                onClick={resetWeightsToOptimal}
                className="flex items-center gap-1.5 text-xs text-nexus-blue hover:text-nexus-blue/80 transition-colors"
              >
                <RefreshCw size={12} />
                Reset to Brier-Optimal
              </button>
            </div>

            <div className="space-y-4">
              {weights.map((w) => (
                <div key={w.agent}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium text-white">{w.agent} Agent</span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted">Optimal: {w.optimal.toFixed(2)}</span>
                      <span className="text-sm font-mono text-nexus-blue">{w.weight.toFixed(2)}</span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min={0.1}
                    max={1.0}
                    step={0.01}
                    value={w.weight}
                    onChange={(e) => updateWeight(w.agent, parseFloat(e.target.value))}
                    className="w-full h-1.5 bg-border rounded-full appearance-none cursor-pointer"
                    style={{
                      background: `linear-gradient(to right, #0088ff ${w.weight * 100}%, #1e1e2e ${w.weight * 100}%)`,
                    }}
                  />
                  <div className="flex justify-between text-xs text-muted mt-0.5">
                    <span>0.1</span>
                    <span className={cn(w.weight !== w.optimal ? 'text-nexus-yellow' : 'text-muted')}>
                      {w.weight !== w.optimal ? 'Modified' : 'Optimal'}
                    </span>
                    <span>1.0</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Risk Limits */}
          <div className="nexus-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <Shield size={16} className="text-nexus-yellow" />
              <h2 className="font-semibold text-white">Risk Limits</h2>
            </div>
            <div className="space-y-3">
              {limits.map((limit) => (
                <div key={limit.key} className="flex items-center justify-between gap-3">
                  <label className="text-xs text-gray-300 flex-1">{limit.label}</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={limit.value}
                      min={limit.min}
                      max={limit.max}
                      onChange={(e) => updateLimit(limit.key, parseFloat(e.target.value))}
                      className="w-16 bg-background border border-border rounded px-2 py-1 text-white text-sm font-mono text-center focus:outline-none focus:border-nexus-blue"
                    />
                    <span className="text-xs text-muted">%</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Circuit Breaker Thresholds */}
      <div className="nexus-card p-5">
        <div className="flex items-center gap-2 mb-4">
          <AlertTriangle size={16} className="text-nexus-red" />
          <h2 className="font-semibold text-white">Circuit Breaker Thresholds</h2>
        </div>
        <div className="grid grid-cols-5 gap-4">
          {thresholds.map((t) => (
            <div key={t.key} className="text-center">
              <div className="text-xs text-muted mb-2">{t.label}</div>
              <div className="flex items-center justify-center gap-1">
                <button
                  onClick={() => updateThreshold(t.key, Math.max(0, +(t.value - t.step).toFixed(2)))}
                  className="w-6 h-6 rounded bg-border text-white text-xs hover:bg-border-bright transition-colors"
                >
                  -
                </button>
                <span className="font-mono text-white text-sm w-12 text-center">
                  {t.value.toFixed(t.step < 1 ? 2 : 1)}
                </span>
                <button
                  onClick={() => updateThreshold(t.key, +(t.value + t.step).toFixed(2))}
                  className="w-6 h-6 rounded bg-border text-white text-xs hover:bg-border-bright transition-colors"
                >
                  +
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* API Connections */}
      <div className="nexus-card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Wifi size={16} className="text-nexus-green" />
          <h2 className="font-semibold text-white">API Connections</h2>
        </div>
        <div className="grid grid-cols-4 gap-3">
          {API_CONNECTIONS.map((api) => (
            <div
              key={api.name}
              className={cn(
                'p-3 rounded-lg border',
                api.status === 'connected'
                  ? 'border-nexus-green/20 bg-nexus-green/5'
                  : 'border-nexus-red/30 bg-nexus-red/5'
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-white">{api.name}</span>
                {api.status === 'connected' ? (
                  <Wifi size={12} className="text-nexus-green" />
                ) : (
                  <WifiOff size={12} className="text-nexus-red" />
                )}
              </div>
              <div className="text-xs text-muted">{api.type}</div>
              {api.status === 'connected' ? (
                <div className="text-xs text-nexus-green mt-1">
                  {api.ping}ms latency
                </div>
              ) : (
                <div className="text-xs text-nexus-red mt-1">
                  Last seen: {formatTimeAgo(api.lastPing)}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Emergency Stop */}
      <EmergencyStopDialog />
    </div>
  );
}
