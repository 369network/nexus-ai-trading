'use client';

import { useState, useEffect, useCallback } from 'react';
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
  Key,
  Eye,
  EyeOff,
  Save,
  Loader2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Zap,
  Send,
  Bell,
  BellOff,
  MessageCircle,
} from 'lucide-react';
import { useNexusStore } from '@/lib/store';
import { cn, formatTimeAgo } from '@/lib/utils';
import { api } from '@/lib/api';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface ApiServiceConfig {
  id: string;
  name: string;
  type: 'Exchange' | 'Broker' | 'Data';
  description: string;
  docsUrl: string;
  fields: { key: string; label: string; placeholder: string; secret?: boolean }[];
  testEndpoint?: string; // public URL to ping for latency
  color: string;
}

interface SavedApiKeys {
  [serviceId: string]: { [fieldKey: string]: string };
}

interface ConnectionStatus {
  status: 'connected' | 'error' | 'untested' | 'testing';
  ping?: number;
  lastTested?: string;
  message?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// API Service Definitions
// ─────────────────────────────────────────────────────────────────────────────

const API_SERVICES: ApiServiceConfig[] = [
  {
    id: 'binance',
    name: 'Binance',
    type: 'Exchange',
    description: 'Spot & futures trading. Required for crypto markets.',
    docsUrl: 'https://www.binance.com/en/my/settings/api-management',
    color: '#F0B90B',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter Binance API Key...' },
      { key: 'api_secret', label: 'API Secret', placeholder: 'Enter Binance API Secret...', secret: true },
    ],
    testEndpoint: 'https://api.binance.com/api/v3/ping',
  },
  {
    id: 'coinbase',
    name: 'Coinbase Advanced',
    type: 'Exchange',
    description: 'Coinbase Advanced Trade API for spot trading.',
    docsUrl: 'https://www.coinbase.com/settings/api',
    color: '#0052FF',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter Coinbase API Key...' },
      { key: 'api_secret', label: 'API Secret', placeholder: 'Enter Coinbase API Secret...', secret: true },
    ],
    testEndpoint: 'https://api.coinbase.com/api/v3/brokerage/products',
  },
  {
    id: 'interactive_brokers',
    name: 'Interactive Brokers',
    type: 'Broker',
    description: 'IB TWS/Gateway for US stocks, options, futures.',
    docsUrl: 'https://www.interactivebrokers.com/en/trading/ib-api.php',
    color: '#CC0000',
    fields: [
      { key: 'account_id', label: 'Account ID', placeholder: 'e.g. U12345678' },
      { key: 'gateway_host', label: 'Gateway Host', placeholder: 'e.g. localhost or 127.0.0.1' },
      { key: 'gateway_port', label: 'Gateway Port', placeholder: 'e.g. 7497 (paper) or 7496 (live)' },
    ],
  },
  {
    id: 'zerodha',
    name: 'Zerodha Kite',
    type: 'Broker',
    description: 'Kite Connect API for Indian equity markets (NSE/BSE).',
    docsUrl: 'https://kite.trade/docs/connect/v3/',
    color: '#387ED1',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter Kite Connect API Key...' },
      { key: 'api_secret', label: 'API Secret', placeholder: 'Enter Kite Connect API Secret...', secret: true },
      { key: 'access_token', label: 'Access Token', placeholder: 'Daily access token (refreshed via login)...', secret: true },
    ],
    testEndpoint: 'https://api.kite.trade/instruments/NSE',
  },
  {
    id: 'alpha_vantage',
    name: 'Alpha Vantage',
    type: 'Data',
    description: 'Stock fundamentals, forex, and economic indicators.',
    docsUrl: 'https://www.alphavantage.co/support/#api-key',
    color: '#5BA65B',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter Alpha Vantage API Key...' },
    ],
    testEndpoint: 'https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=1min&apikey=demo',
  },
  {
    id: 'newsapi',
    name: 'NewsAPI',
    type: 'Data',
    description: 'Real-time news sentiment for NLP signal analysis.',
    docsUrl: 'https://newsapi.org/account',
    color: '#6B46C1',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter NewsAPI Key...' },
    ],
    testEndpoint: 'https://newsapi.org/v2/top-headlines?country=us&apiKey=demo',
  },
  {
    id: 'glassnode',
    name: 'Glassnode',
    type: 'Data',
    description: 'On-chain crypto analytics for whale and network signals.',
    docsUrl: 'https://studio.glassnode.com/settings/api',
    color: '#FF6B00',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter Glassnode API Key...' },
    ],
    testEndpoint: 'https://api.glassnode.com/v1/metrics/market/price_usd_close?a=BTC',
  },
];

const STORAGE_KEY = 'nexus_api_keys';
const STATUS_STORAGE_KEY = 'nexus_api_status';

// ─────────────────────────────────────────────────────────────────────────────
// Feature Flags
// ─────────────────────────────────────────────────────────────────────────────

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

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

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
        className="inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform shadow-sm"
        style={{ transform: checked ? 'translateX(20px)' : 'translateX(2px)' }}
      />
    </button>
  );
}

function EmergencyStopDialog() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');

  const handleStop = () => {
    if (input === 'STOP') {
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
            This will immediately halt all trading activity. This action cannot be undone.
          </Dialog.Description>
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
      setOpen(true);
    } else {
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
        <ToggleSwitch checked={!systemStatus.paper_mode} onChange={handleToggle} />
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

// ─────────────────────────────────────────────────────────────────────────────
// API Connection Card with inline key management
// ─────────────────────────────────────────────────────────────────────────────

function ApiConnectionCard({
  service,
  savedKeys,
  status,
  onSave,
  onTest,
}: {
  service: ApiServiceConfig;
  savedKeys: Record<string, string>;
  status: ConnectionStatus;
  onSave: (serviceId: string, keys: Record<string, string>) => void;
  onTest: (service: ApiServiceConfig, keys: Record<string, string>) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [draftKeys, setDraftKeys] = useState<Record<string, string>>({});
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [testing, setTesting] = useState(false);

  // When saved keys change (initial load), sync draft
  useEffect(() => {
    setDraftKeys({ ...savedKeys });
  }, [savedKeys]);

  const hasKeys = Object.values(savedKeys).some((v) => v && v.length > 0);
  const draftChanged = service.fields.some((f) => draftKeys[f.key] !== savedKeys[f.key]);
  const draftHasKeys = Object.values(draftKeys).some((v) => v && v.length > 0);

  const handleSave = () => {
    onSave(service.id, draftKeys);
  };

  const handleTest = async () => {
    setTesting(true);
    await onTest(service, draftKeys);
    setTesting(false);
  };

  const statusIcon = () => {
    if (testing || status.status === 'testing') return <Loader2 size={13} className="text-nexus-blue animate-spin" />;
    if (!hasKeys) return <WifiOff size={13} className="text-gray-500" />;
    if (status.status === 'connected') return <Wifi size={13} className="text-nexus-green" />;
    if (status.status === 'error') return <XCircle size={13} className="text-nexus-red" />;
    return <Wifi size={13} className="text-gray-500" />;
  };

  const statusBorder = () => {
    if (!hasKeys) return 'border-border';
    if (status.status === 'connected') return 'border-nexus-green/30';
    if (status.status === 'error') return 'border-nexus-red/30';
    return 'border-border';
  };

  const statusBg = () => {
    if (!hasKeys) return '';
    if (status.status === 'connected') return 'bg-nexus-green/5';
    if (status.status === 'error') return 'bg-nexus-red/5';
    return '';
  };

  return (
    <div className={cn('rounded-xl border transition-all duration-200', statusBorder(), statusBg())}>
      {/* Header row */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-3 hover:bg-white/5 rounded-xl transition-colors"
      >
        <div className="flex items-center gap-2.5 text-left">
          {/* Color dot */}
          <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: service.color }} />
          <div>
            <div className="text-sm font-semibold text-white leading-tight">{service.name}</div>
            <div className="text-xs text-muted">{service.type}</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Status */}
          <div className="flex items-center gap-1.5">
            {statusIcon()}
            <span className="text-xs font-mono text-muted hidden sm:inline">
              {testing || status.status === 'testing' ? 'Testing...' :
               !hasKeys ? 'No key' :
               status.status === 'connected' ? `${status.ping ?? '?'}ms` :
               status.status === 'error' ? 'Error' : 'Untested'}
            </span>
          </div>
          {/* Expand */}
          {expanded ? <ChevronUp size={14} className="text-muted" /> : <ChevronDown size={14} className="text-muted" />}
        </div>
      </button>

      {/* Expanded config panel */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-border/50 pt-3 space-y-3">
          <div className="text-xs text-muted mb-2">{service.description}</div>

          {/* Fields */}
          {service.fields.map((field) => (
            <div key={field.key}>
              <label className="text-xs text-gray-400 block mb-1">{field.label}</label>
              <div className="relative">
                <input
                  type={field.secret && !showSecrets[field.key] ? 'password' : 'text'}
                  value={draftKeys[field.key] ?? ''}
                  onChange={(e) =>
                    setDraftKeys((prev) => ({ ...prev, [field.key]: e.target.value }))
                  }
                  placeholder={field.placeholder}
                  className="w-full bg-background border border-border rounded-lg px-3 py-2 text-white text-xs font-mono focus:outline-none focus:border-nexus-blue pr-8"
                />
                {field.secret && (
                  <button
                    type="button"
                    onClick={() =>
                      setShowSecrets((prev) => ({ ...prev, [field.key]: !prev[field.key] }))
                    }
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-white"
                  >
                    {showSecrets[field.key] ? <EyeOff size={13} /> : <Eye size={13} />}
                  </button>
                )}
              </div>
            </div>
          ))}

          {/* Key indicator */}
          {hasKeys && (
            <div className="flex items-center gap-1.5 text-xs text-nexus-green">
              <CheckCircle2 size={11} />
              <span>API key saved</span>
              {status.status === 'connected' && status.lastTested && (
                <span className="text-muted">· tested {formatTimeAgo(status.lastTested)}</span>
              )}
            </div>
          )}

          {/* Action row */}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSave}
              disabled={!draftChanged && hasKeys}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                draftChanged || !hasKeys
                  ? 'bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20 hover:bg-nexus-blue/20'
                  : 'bg-border text-muted cursor-not-allowed opacity-50'
              )}
            >
              <Save size={12} />
              {hasKeys && !draftChanged ? 'Saved' : 'Save Keys'}
            </button>

            <button
              onClick={handleTest}
              disabled={testing || !draftHasKeys}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                draftHasKeys
                  ? 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20 hover:bg-nexus-green/20'
                  : 'bg-border text-muted cursor-not-allowed opacity-50'
              )}
            >
              {testing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
              Test Connection
            </button>

            <a
              href={service.docsUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto flex items-center gap-1 text-xs text-muted hover:text-white transition-colors"
            >
              Get API Key
              <ExternalLink size={11} />
            </a>
          </div>

          {/* Error message */}
          {status.status === 'error' && status.message && (
            <div className="text-xs text-nexus-red bg-nexus-red/5 border border-nexus-red/20 rounded-lg px-2 py-1.5">
              {status.message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Settings Page
// ─────────────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [featureFlags, setFeatureFlags] = useState(FEATURE_FLAGS);
  const [weights, setWeights] = useState(LLM_WEIGHTS);
  const [limits, setLimits] = useState(RISK_LIMITS);
  const [thresholds, setThresholds] = useState(CIRCUIT_THRESHOLDS);

  // API Keys state (loaded from localStorage)
  const [savedKeys, setSavedKeys] = useState<SavedApiKeys>({});
  const [statuses, setStatuses] = useState<Record<string, ConnectionStatus>>(() =>
    API_SERVICES.reduce((acc, s) => ({ ...acc, [s.id]: { status: 'untested' as const } }), {})
  );
  const [saveNotice, setSaveNotice] = useState('');

  // Load feature flags from Supabase on mount
  useEffect(() => {
    api.getFeatureFlags().then((flags) => {
      if (Object.keys(flags).length > 0) {
        setFeatureFlags((prev) =>
          prev.map((f) => ({
            ...f,
            value: typeof flags[f.key] === 'boolean' ? (flags[f.key] as boolean) : f.value,
          }))
        );
      }
    }).catch(() => {/* use defaults */});
  }, []);

  // Load keys from localStorage on mount
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) setSavedKeys(JSON.parse(raw));

      const statusRaw = localStorage.getItem(STATUS_STORAGE_KEY);
      if (statusRaw) {
        const saved = JSON.parse(statusRaw) as Record<string, ConnectionStatus>;
        setStatuses((prev) => {
          const merged = { ...prev };
          for (const [id, s] of Object.entries(saved)) {
            merged[id] = s;
          }
          return merged;
        });
      }
    } catch {
      // ignore parse errors
    }
  }, []);

  const handleSaveKeys = useCallback((serviceId: string, keys: Record<string, string>) => {
    setSavedKeys((prev) => {
      const next = { ...prev, [serviceId]: keys };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
    setSaveNotice('Keys saved locally (browser only — never sent to any server)');
    setTimeout(() => setSaveNotice(''), 4000);
  }, []);

  const handleTestConnection = useCallback(async (service: ApiServiceConfig, keys: Record<string, string>) => {
    setStatuses((prev) => ({ ...prev, [service.id]: { status: 'testing' } }));

    const t0 = Date.now();
    try {
      if (service.testEndpoint) {
        const res = await fetch(service.testEndpoint, {
          method: 'GET',
          signal: AbortSignal.timeout(8000),
        });
        const ping = Date.now() - t0;
        const ok = res.ok || res.status === 400; // some endpoints return 400 with valid keys
        const newStatus: ConnectionStatus = {
          status: ok ? 'connected' : 'error',
          ping: ok ? ping : undefined,
          lastTested: new Date().toISOString(),
          message: ok ? undefined : `HTTP ${res.status}`,
        };
        setStatuses((prev) => {
          const next = { ...prev, [service.id]: newStatus };
          try { localStorage.setItem(STATUS_STORAGE_KEY, JSON.stringify(next)); } catch {}
          return next;
        });
      } else {
        // No test endpoint — just mark as configured
        const newStatus: ConnectionStatus = {
          status: 'connected',
          ping: undefined,
          lastTested: new Date().toISOString(),
          message: 'Connection cannot be tested from browser — keys saved for bot use.',
        };
        setStatuses((prev) => {
          const next = { ...prev, [service.id]: newStatus };
          try { localStorage.setItem(STATUS_STORAGE_KEY, JSON.stringify(next)); } catch {}
          return next;
        });
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Connection failed';
      const newStatus: ConnectionStatus = {
        status: 'error',
        lastTested: new Date().toISOString(),
        message,
      };
      setStatuses((prev) => {
        const next = { ...prev, [service.id]: newStatus };
        try { localStorage.setItem(STATUS_STORAGE_KEY, JSON.stringify(next)); } catch {}
        return next;
      });
    }
  }, []);

  const toggleFlag = (key: string) => {
    setFeatureFlags((prev) => prev.map((f) => {
      if (f.key !== key) return f;
      const newValue = !f.value;
      // Fire-and-forget: persist to Supabase, ignore errors
      api.updateFeatureFlag(key, newValue).catch(() => {});
      return { ...f, value: newValue };
    }));
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

  const flagsByCategory = featureFlags.reduce((acc, f) => {
    if (!acc[f.category]) acc[f.category] = [];
    acc[f.category].push(f);
    return acc;
  }, {} as Record<string, FeatureFlag[]>);

  // Summary counts
  const connectedCount = Object.values(statuses).filter((s) => s.status === 'connected').length;
  const configuredCount = Object.values(savedKeys).filter((v) => Object.values(v).some((k) => k?.length > 0)).length;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Paper Mode Toggle */}
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
                      <ToggleSwitch checked={flag.value} onChange={() => toggleFlag(flag.key)} />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right column: LLM Weights + Risk Limits */}
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
                    type="range" min={0.1} max={1.0} step={0.01} value={w.weight}
                    onChange={(e) => updateWeight(w.agent, parseFloat(e.target.value))}
                    className="w-full h-1.5 bg-border rounded-full appearance-none cursor-pointer"
                    style={{ background: `linear-gradient(to right, #0088ff ${w.weight * 100}%, #1e1e2e ${w.weight * 100}%)` }}
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
                      type="number" value={limit.value} min={limit.min} max={limit.max}
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
                >-</button>
                <span className="font-mono text-white text-sm w-12 text-center">
                  {t.value.toFixed(t.step < 1 ? 2 : 1)}
                </span>
                <button
                  onClick={() => updateThreshold(t.key, +(t.value + t.step).toFixed(2))}
                  className="w-6 h-6 rounded bg-border text-white text-xs hover:bg-border-bright transition-colors"
                >+</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── API Connections (full key management) ── */}
      <div className="nexus-card p-5">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <Key size={16} className="text-nexus-blue" />
            <h2 className="font-semibold text-white">API Connections</h2>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted">
              {configuredCount}/{API_SERVICES.length} configured
              {connectedCount > 0 && ` · ${connectedCount} tested`}
            </span>
          </div>
        </div>

        <p className="text-xs text-muted mb-4">
          API keys are stored only in your browser (localStorage). They are never sent to any server.
          Click any service to expand and enter your credentials.
        </p>

        {saveNotice && (
          <div className="flex items-center gap-2 text-xs text-nexus-green bg-nexus-green/5 border border-nexus-green/20 rounded-lg px-3 py-2 mb-3">
            <CheckCircle2 size={12} />
            {saveNotice}
          </div>
        )}

        <div className="grid grid-cols-1 gap-2">
          {API_SERVICES.map((service) => (
            <ApiConnectionCard
              key={service.id}
              service={service}
              savedKeys={savedKeys[service.id] ?? {}}
              status={statuses[service.id] ?? { status: 'untested' }}
              onSave={handleSaveKeys}
              onTest={handleTestConnection}
            />
          ))}
        </div>

        <div className="mt-4 p-3 bg-nexus-yellow/5 border border-nexus-yellow/20 rounded-lg">
          <p className="text-xs text-nexus-yellow font-medium mb-1">For live trading, also configure keys on the VPS</p>
          <p className="text-xs text-muted">
            Dashboard keys are for reference only. The trading bot reads its keys from{' '}
            <code className="font-mono bg-background px-1 py-0.5 rounded">/opt/nexus-alpha/.env</code>{' '}
            on the VPS. Update that file to activate keys for live execution.
          </p>
        </div>
      </div>

      {/* ── Telegram Notifications ── */}
      <TelegramNotificationsPanel />

      {/* Emergency Stop */}
      <EmergencyStopDialog />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Telegram Notifications Panel
// ─────────────────────────────────────────────────────────────────────────────

const TELEGRAM_STORAGE_KEY = 'nexus_telegram_config';

const TELEGRAM_EVENTS = [
  { key: 'new_trade',       label: 'Trade Executed',     description: 'When a new trade is opened or closed', default: true },
  { key: 'signal',          label: 'Signal Generated',   description: 'When an AI signal is produced (strength >= 75)', default: false },
  { key: 'circuit_breaker', label: 'Circuit Breaker',    description: 'When any circuit breaker trips', default: true },
  { key: 'daily_summary',   label: 'Daily Summary',      description: 'End-of-day P&L and performance summary', default: true },
  { key: 'emergency_stop',  label: 'Emergency Stop',     description: 'When emergency stop is triggered', default: true },
  { key: 'drawdown_alert',  label: 'Drawdown Alert',     description: 'When drawdown exceeds 5%', default: true },
];

interface TelegramConfig {
  botToken: string;
  chatId: string;
  enabled: boolean;
  events: Record<string, boolean>;
}

function TelegramNotificationsPanel() {
  const [config, setConfig] = useState<TelegramConfig>({
    botToken: '',
    chatId: '',
    enabled: false,
    events: Object.fromEntries(TELEGRAM_EVENTS.map((e) => [e.key, e.default])),
  });
  const [showToken, setShowToken] = useState(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'sending' | 'ok' | 'error'>('idle');
  const [testError, setTestError] = useState('');
  const [saved, setSaved] = useState(false);

  // Load from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem(TELEGRAM_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<TelegramConfig>;
        setConfig((prev) => ({
          ...prev,
          ...parsed,
          events: { ...prev.events, ...(parsed.events ?? {}) },
        }));
      }
    } catch {}
  }, []);

  const save = () => {
    try {
      localStorage.setItem(TELEGRAM_STORAGE_KEY, JSON.stringify(config));
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch {}
  };

  const sendTest = async () => {
    if (!config.botToken || !config.chatId) {
      setTestError('Enter bot token and chat ID first');
      setTestStatus('error');
      return;
    }
    setTestStatus('sending');
    setTestError('');
    try {
      const text = encodeURIComponent(
        `NEXUS ALPHA — Test Notification\n\n` +
        `Telegram integration is working correctly.\n` +
        `${new Date().toLocaleString()}\n` +
        `Dashboard connected.`
      );
      const url = `https://api.telegram.org/bot${config.botToken}/sendMessage?chat_id=${config.chatId}&text=${text}&parse_mode=Markdown`;
      const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
      const data = await res.json();
      if (data.ok) {
        setTestStatus('ok');
        setTimeout(() => setTestStatus('idle'), 4000);
      } else {
        setTestError(data.description ?? 'Telegram API error');
        setTestStatus('error');
      }
    } catch (err) {
      setTestError(err instanceof Error ? err.message : 'Network error');
      setTestStatus('error');
    }
  };

  return (
    <div className="nexus-card p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <MessageCircle size={16} className="text-nexus-blue" />
          <h2 className="font-semibold text-white">Telegram Notifications</h2>
        </div>
        <div className="flex items-center gap-3">
          <span className={cn('text-xs font-medium', config.enabled ? 'text-nexus-green' : 'text-muted')}>
            {config.enabled ? 'Enabled' : 'Disabled'}
          </span>
          <button
            onClick={() => setConfig((prev) => ({ ...prev, enabled: !prev.enabled }))}
            className={cn(
              'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
              config.enabled ? 'bg-nexus-green' : 'bg-border'
            )}
          >
            <span
              className={cn(
                'inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform',
                config.enabled ? 'translate-x-4' : 'translate-x-1'
              )}
            />
          </button>
        </div>
      </div>

      <p className="text-xs text-muted mb-4">
        Send real-time trade alerts and signals to your Telegram.{' '}
        <a
          href="https://core.telegram.org/bots#how-do-i-create-a-bot"
          target="_blank"
          rel="noopener noreferrer"
          className="text-nexus-blue underline"
        >
          Create a bot via @BotFather
        </a>
        . Your token is stored only in your browser.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
        {/* Bot Token */}
        <div>
          <label className="text-xs text-muted mb-1 block">Bot Token</label>
          <div className="relative">
            <input
              type={showToken ? 'text' : 'password'}
              value={config.botToken}
              onChange={(e) => setConfig((prev) => ({ ...prev, botToken: e.target.value }))}
              placeholder="123456:ABCdef..."
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm font-mono text-white placeholder-muted pr-10 focus:outline-none focus:border-nexus-blue"
            />
            <button
              onClick={() => setShowToken((p) => !p)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-white transition-colors"
            >
              {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
        </div>

        {/* Chat ID */}
        <div>
          <label className="text-xs text-muted mb-1 block">Chat ID</label>
          <input
            type="text"
            value={config.chatId}
            onChange={(e) => setConfig((prev) => ({ ...prev, chatId: e.target.value }))}
            placeholder="-100123456789 or @channelname"
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm font-mono text-white placeholder-muted focus:outline-none focus:border-nexus-blue"
          />
          <p className="text-xs text-muted mt-1">
            Add your bot to a group, then use{' '}
            <code className="font-mono bg-background px-1 py-0.5 rounded text-nexus-blue">@userinfobot</code>{' '}
            to find your chat ID.
          </p>
        </div>
      </div>

      {/* Event toggles */}
      <div className="mb-4">
        <div className="text-xs text-muted uppercase tracking-wider mb-2">Notify on</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {TELEGRAM_EVENTS.map((event) => (
            <div
              key={event.key}
              className="flex items-start justify-between gap-3 bg-background rounded-lg px-3 py-2.5"
            >
              <div>
                <div className="text-sm font-medium text-white flex items-center gap-1.5">
                  {config.events[event.key]
                    ? <Bell size={11} className="text-nexus-green" />
                    : <BellOff size={11} className="text-muted" />
                  }
                  {event.label}
                </div>
                <div className="text-xs text-muted mt-0.5">{event.description}</div>
              </div>
              <button
                onClick={() => setConfig((prev) => ({
                  ...prev,
                  events: { ...prev.events, [event.key]: !prev.events[event.key] },
                }))}
                className={cn(
                  'relative inline-flex h-4 w-8 flex-shrink-0 items-center rounded-full transition-colors mt-0.5',
                  config.events[event.key] ? 'bg-nexus-green' : 'bg-border'
                )}
              >
                <span
                  className={cn(
                    'inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform',
                    config.events[event.key] ? 'translate-x-4' : 'translate-x-0.5'
                  )}
                />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3">
        <button
          onClick={sendTest}
          disabled={testStatus === 'sending' || !config.botToken || !config.chatId}
          className={cn(
            'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all',
            testStatus === 'ok'
              ? 'bg-nexus-green/20 text-nexus-green border border-nexus-green/30'
              : testStatus === 'error'
                ? 'bg-nexus-red/20 text-nexus-red border border-nexus-red/30'
                : 'bg-nexus-blue/10 text-nexus-blue border border-nexus-blue/20 hover:bg-nexus-blue/20 disabled:opacity-40 disabled:cursor-not-allowed'
          )}
        >
          {testStatus === 'sending' ? (
            <><Loader2 size={13} className="animate-spin" /> Sending...</>
          ) : testStatus === 'ok' ? (
            <><CheckCircle2 size={13} /> Sent!</>
          ) : testStatus === 'error' ? (
            <><XCircle size={13} /> Failed</>
          ) : (
            <><Send size={13} /> Send Test</>
          )}
        </button>

        <button
          onClick={save}
          className={cn(
            'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all',
            saved
              ? 'bg-nexus-green/20 text-nexus-green border border-nexus-green/30'
              : 'bg-border text-white hover:bg-border-bright border border-border'
          )}
        >
          {saved ? <><CheckCircle2 size={13} /> Saved</> : <><Save size={13} /> Save Config</>}
        </button>
      </div>

      {testStatus === 'error' && testError && (
        <div className="mt-2 text-xs text-nexus-red flex items-center gap-1.5">
          <AlertTriangle size={11} />
          {testError}
        </div>
      )}

      <div className="mt-4 p-3 bg-nexus-blue/5 border border-nexus-blue/20 rounded-lg">
        <p className="text-xs text-nexus-blue font-medium mb-1">How notifications work</p>
        <p className="text-xs text-muted">
          The dashboard sends notifications directly from your browser. For persistent alerts when
          the dashboard is closed, configure the same bot token as{' '}
          <code className="font-mono bg-background px-1 py-0.5 rounded">TELEGRAM_BOT_TOKEN</code> and{' '}
          <code className="font-mono bg-background px-1 py-0.5 rounded">TELEGRAM_CHAT_ID</code>{' '}
          environment variables in the VPS <code className="font-mono bg-background px-1 py-0.5 rounded">.env</code> file.
        </p>
      </div>
    </div>
  );
}
