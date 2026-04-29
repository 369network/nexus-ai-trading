'use client';

import { useEffect, useState } from 'react';
import { AgentNetwork } from '@/components/AgentNetwork';
import { useNexusStore } from '@/lib/store';
import { getBrierScores, getAgentHistory } from '@/lib/supabase';
import { cn, formatPercent, formatTimeAgo } from '@/lib/utils';
import type { AgentDecision, BrierScore, AgentRole } from '@/lib/types';

const AGENT_ROLES: { role: AgentRole; label: string; description: string }[] = [
  { role: 'bull', label: 'Bull Agent', description: 'Seeks long opportunities via momentum & breakouts' },
  { role: 'bear', label: 'Bear Agent', description: 'Identifies shorting opportunities & weakness' },
  { role: 'fundamental', label: 'Fundamental', description: 'Macro & fundamentals: earnings, rates, GDP' },
  { role: 'technical', label: 'Technical', description: 'Price action, indicators, chart patterns' },
  { role: 'sentiment', label: 'Sentiment', description: 'News, social, options flow, positioning' },
  { role: 'risk', label: 'Risk Manager', description: 'Portfolio risk, correlation, drawdown' },
  { role: 'portfolio', label: 'Portfolio', description: 'Allocation, sizing, final decision' },
];

const MARKETS = ['crypto', 'forex', 'commodities', 'indian_stocks', 'us_stocks'] as const;

function BrierScoreMatrix({ scores }: { scores: BrierScore[] }) {
  const getScore = (agent: AgentRole, market: string) => {
    return scores.find((s) => s.agent === agent && s.market === market);
  };

  const getColor = (score: number) => {
    if (score < 0.15) return 'bg-nexus-green/20 text-nexus-green';
    if (score < 0.20) return 'bg-nexus-blue/20 text-nexus-blue';
    if (score < 0.25) return 'bg-nexus-yellow/20 text-nexus-yellow';
    return 'bg-nexus-red/20 text-nexus-red';
  };

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Brier Score Matrix</h3>
        <span className="text-xs text-muted">Lower = Better</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="text-left pb-2 font-normal text-muted w-24">Agent</th>
              {MARKETS.map((m) => (
                <th key={m} className="text-center pb-2 font-normal text-muted capitalize">
                  {m.replace('_', ' ')}
                </th>
              ))}
              <th className="text-center pb-2 font-normal text-muted">Avg</th>
            </tr>
          </thead>
          <tbody>
            {AGENT_ROLES.slice(0, 5).map(({ role, label }) => {
              const agentScores = MARKETS.map((m) => getScore(role, m)?.score ?? null);
              const validScores = agentScores.filter((s): s is number => s !== null);
              const avg = validScores.length > 0
                ? validScores.reduce((s, v) => s + v, 0) / validScores.length
                : null;
              return (
                <tr key={role}>
                  <td className="py-1.5 pr-3 text-white font-medium capitalize">{label.split(' ')[0]}</td>
                  {MARKETS.map((m, i) => {
                    const s = agentScores[i];
                    return (
                      <td key={m} className="text-center py-1.5 px-2">
                        {s !== null ? (
                          <span className={cn('px-2 py-0.5 rounded text-xs font-mono', getColor(s))}>
                            {s.toFixed(3)}
                          </span>
                        ) : (
                          <span className="text-muted text-xs">—</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="text-center py-1.5">
                    {avg !== null ? (
                      <span className={cn('px-2 py-0.5 rounded text-xs font-mono font-bold', getColor(avg))}>
                        {avg.toFixed(3)}
                      </span>
                    ) : (
                      <span className="text-muted text-xs">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs">
        {[
          { label: '< 0.15 Excellent', color: 'bg-nexus-green/20 text-nexus-green' },
          { label: '< 0.20 Good', color: 'bg-nexus-blue/20 text-nexus-blue' },
          { label: '< 0.25 Fair', color: 'bg-nexus-yellow/20 text-nexus-yellow' },
          { label: '≥ 0.25 Poor', color: 'bg-nexus-red/20 text-nexus-red' },
        ].map((item) => (
          <span key={item.label} className={cn('badge', item.color)}>
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function AgentDecisionCard({
  role,
  decision,
  isSelected,
  onClick,
}: {
  role: (typeof AGENT_ROLES)[0];
  decision?: AgentDecision;
  isSelected: boolean;
  onClick: () => void;
}) {
  const decisionColor = !decision
    ? 'text-muted'
    : decision.decision === 'LONG'
    ? 'text-nexus-green'
    : decision.decision === 'SHORT'
    ? 'text-nexus-red'
    : 'text-nexus-yellow';

  const borderClass = isSelected
    ? 'border-nexus-blue/40'
    : !decision
    ? 'border-border'
    : decision.decision === 'LONG'
    ? 'border-nexus-green/20'
    : decision.decision === 'SHORT'
    ? 'border-nexus-red/20'
    : 'border-nexus-yellow/20';

  return (
    <button
      onClick={onClick}
      className={cn(
        'nexus-card nexus-card-hover p-3 text-left w-full transition-all',
        borderClass,
        isSelected && 'glow-blue'
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-bold text-white">{role.label}</span>
        <span className={cn('text-xs font-bold', decisionColor)}>
          {decision?.decision ?? 'PENDING'}
        </span>
      </div>
      {decision && (
        <>
          <div className="flex items-center gap-2 mb-1">
            <div className="flex-1 h-1 bg-border rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full',
                  decision.decision === 'LONG' ? 'bg-nexus-green' : decision.decision === 'SHORT' ? 'bg-nexus-red' : 'bg-nexus-yellow'
                )}
                style={{ width: `${decision.confidence * 100}%` }}
              />
            </div>
            <span className="text-xs font-mono text-muted">
              {(decision.confidence * 100).toFixed(0)}%
            </span>
          </div>
          <p className="text-xs text-muted line-clamp-2">{decision.reasoning}</p>
          <div className="text-xs text-muted mt-2">{formatTimeAgo(decision.created_at)}</div>
        </>
      )}
    </button>
  );
}

function DebateReplay({ decisions }: { decisions: AgentDecision[] }) {
  const [step, setStep] = useState(0);

  const visibleDecisions = decisions.slice(0, step + 1);

  return (
    <div className="nexus-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-sm text-white">Debate Replay</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">{step + 1}/{decisions.length || 1}</span>
        </div>
      </div>

      <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
        {visibleDecisions.map((d, i) => (
          <div
            key={d.id || i}
            className={cn(
              'p-3 rounded-lg border text-xs transition-all',
              i === step ? 'border-nexus-blue/30 bg-nexus-blue/5' : 'border-border bg-white/2',
              'animate-fade-in'
            )}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="font-bold text-white capitalize">{d.role} Agent</span>
              <span
                className={cn(
                  'font-bold',
                  d.decision === 'LONG' ? 'text-nexus-green' : d.decision === 'SHORT' ? 'text-nexus-red' : 'text-nexus-yellow'
                )}
              >
                {d.decision} ({(d.confidence * 100).toFixed(0)}%)
              </span>
            </div>
            <p className="text-muted">{d.reasoning}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => setStep(Math.max(0, step - 1))}
          disabled={step === 0}
          className="px-3 py-1.5 rounded-lg bg-border text-xs text-white disabled:opacity-30 hover:bg-border-bright transition-colors"
        >
          ← Prev
        </button>
        <div className="flex-1 h-1 bg-border rounded-full overflow-hidden">
          <div
            className="h-full bg-nexus-blue rounded-full transition-all"
            style={{ width: `${((step + 1) / Math.max(decisions.length, 1)) * 100}%` }}
          />
        </div>
        <button
          onClick={() => setStep(Math.min(decisions.length - 1, step + 1))}
          disabled={step >= decisions.length - 1}
          className="px-3 py-1.5 rounded-lg bg-border text-xs text-white disabled:opacity-30 hover:bg-border-bright transition-colors"
        >
          Next →
        </button>
      </div>
    </div>
  );
}

export default function AgentsPage() {
  const { agentStates } = useNexusStore();
  const [brierScores, setBrierScores] = useState<BrierScore[]>([]);
  const [agentHistory, setAgentHistory] = useState<AgentDecision[]>([]);
  const [selectedRole, setSelectedRole] = useState<AgentRole | null>(null);
  const [roleHistory, setRoleHistory] = useState<AgentDecision[]>([]);

  useEffect(() => {
    const load = async () => {
      const [scores, history] = await Promise.all([
        getBrierScores(),
        getAgentHistory(undefined, 50),
      ]);
      setBrierScores(scores);
      setAgentHistory(history);
    };
    load();
  }, []);

  useEffect(() => {
    if (selectedRole) {
      getAgentHistory(selectedRole, 5).then(setRoleHistory);
    }
  }, [selectedRole]);

  return (
    <div className="space-y-4">
      {/* D3 Network + Agent cards */}
      <div className="grid grid-cols-3 gap-4">
        {/* D3 Force Graph */}
        <div className="col-span-2 nexus-card" style={{ height: 420 }}>
          <AgentNetwork
            agentStates={agentStates}
            onAgentClick={(role) => setSelectedRole(role as AgentRole)}
          />
        </div>

        {/* Agent decision cards */}
        <div className="space-y-2">
          <h3 className="font-medium text-sm text-white mb-2">Agent Decisions</h3>
          {AGENT_ROLES.map((role) => (
            <AgentDecisionCard
              key={role.role}
              role={role}
              decision={agentStates[role.role]}
              isSelected={selectedRole === role.role}
              onClick={() => setSelectedRole(selectedRole === role.role ? null : role.role)}
            />
          ))}
        </div>
      </div>

      {/* Selected agent detail + debate replay */}
      {selectedRole && (
        <div className="grid grid-cols-2 gap-4">
          {/* Agent detail */}
          <div className="nexus-card p-4">
            <h3 className="font-medium text-sm text-white mb-3">
              {AGENT_ROLES.find((r) => r.role === selectedRole)?.label} — Last 5 Decisions
            </h3>
            <div className="space-y-3">
              {roleHistory.map((d, i) => (
                <div key={d.id || i} className="p-3 rounded-lg bg-white/2 border border-border">
                  <div className="flex items-center justify-between mb-1">
                    <span
                      className={cn(
                        'badge text-xs',
                        d.decision === 'LONG' ? 'bg-nexus-green/10 text-nexus-green border border-nexus-green/20' :
                        d.decision === 'SHORT' ? 'bg-nexus-red/10 text-nexus-red border border-nexus-red/20' :
                        'bg-nexus-yellow/10 text-nexus-yellow border border-nexus-yellow/20'
                      )}
                    >
                      {d.decision}
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-nexus-blue">
                        {(d.confidence * 100).toFixed(0)}%
                      </span>
                      <span className="text-xs text-muted">{formatTimeAgo(d.created_at)}</span>
                    </div>
                  </div>
                  <p className="text-xs text-gray-300 leading-relaxed">{d.reasoning}</p>
                  {d.key_factors && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {d.key_factors.map((f) => (
                        <span key={f} className="text-xs bg-nexus-blue/10 text-nexus-blue rounded px-1.5 py-0.5">
                          {f}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Debate replay */}
          <DebateReplay decisions={agentHistory.slice(0, 7)} />
        </div>
      )}

      {/* Brier score matrix */}
      <BrierScoreMatrix scores={brierScores} />
    </div>
  );
}
