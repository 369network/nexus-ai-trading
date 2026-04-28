'use client';

import { useMemo, useState } from 'react';

// ─── Types ────────────────────────────────────────────────────

export interface DayData {
  date: string;    // YYYY-MM-DD
  pnl: number;     // dollar PnL for the day
  pnlPct: number;  // percentage PnL
  trades: number;  // number of trades that day
}

export interface PnLCalendarProps {
  data: DayData[];
  year?: number;
}

// ─── Color helpers ────────────────────────────────────────────

function getPnlColor(pnlPct: number | null): string {
  if (pnlPct === null) return 'rgba(31,31,48,0.5)'; // bg-gray-800/50 equivalent

  if (pnlPct > 2)    return '#00ff88';
  if (pnlPct > 1)    return '#00cc6a';
  if (pnlPct > 0.5)  return '#009950';
  if (pnlPct > 0.1)  return '#006635';
  if (pnlPct > -0.1) return 'rgba(31,31,48,0.5)'; // essentially flat
  if (pnlPct > -0.5) return '#4a1515';
  if (pnlPct > -1)   return '#7a1f1f';
  if (pnlPct > -2)   return '#b32929';
  return '#ff4444';
}

// ─── Tooltip ──────────────────────────────────────────────────

interface TooltipData {
  date: string;
  pnl: number;
  pnlPct: number;
  trades: number;
  x: number;
  y: number;
}

function CalendarTooltip({ tip }: { tip: TooltipData }) {
  const pnlStr = tip.pnl >= 0
    ? `+$${tip.pnl.toFixed(2)}`
    : `-$${Math.abs(tip.pnl).toFixed(2)}`;
  const pctStr = tip.pnlPct >= 0
    ? `+${tip.pnlPct.toFixed(2)}%`
    : `${tip.pnlPct.toFixed(2)}%`;
  const pnlColor = tip.pnl >= 0 ? '#00ff88' : '#ff4444';

  return (
    <div
      style={{
        position: 'fixed',
        left: tip.x + 12,
        top: tip.y - 8,
        zIndex: 9999,
        background: '#1a1a28',
        border: '1px solid #2a2a3e',
        borderRadius: '6px',
        padding: '8px 10px',
        fontSize: '11px',
        pointerEvents: 'none',
        whiteSpace: 'nowrap',
        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
      }}
    >
      <div style={{ color: '#9ca3af', marginBottom: '4px' }}>{tip.date}</div>
      <div style={{ color: pnlColor, fontWeight: 700, fontFamily: 'monospace', fontSize: '12px' }}>
        {pnlStr} <span style={{ fontWeight: 400, fontSize: '10px' }}>({pctStr})</span>
      </div>
      <div style={{ color: '#6b7280', marginTop: '2px' }}>
        {tip.trades} trade{tip.trades !== 1 ? 's' : ''}
      </div>
    </div>
  );
}

// ─── Legend ───────────────────────────────────────────────────

const LEGEND_STEPS: { color: string; label?: string }[] = [
  { color: '#ff4444', label: 'Loss' },
  { color: '#b32929' },
  { color: '#7a1f1f' },
  { color: '#4a1515' },
  { color: 'rgba(31,31,48,0.5)', label: 'Flat' },
  { color: '#006635' },
  { color: '#009950' },
  { color: '#00cc6a' },
  { color: '#00ff88', label: 'Profit' },
];

// ─── Main Component ───────────────────────────────────────────

const DAYS_OF_WEEK = ['', 'M', '', 'W', '', 'F', ''];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function PnLCalendar({ data, year }: PnLCalendarProps) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const targetYear = year ?? new Date().getFullYear();

  // Build a lookup map of date -> DayData
  const dataMap = useMemo(() => {
    const map: Record<string, DayData> = {};
    for (const d of data) {
      map[d.date] = d;
    }
    return map;
  }, [data]);

  // Build the 52-week × 7 grid
  // Start from Jan 1 of targetYear, aligned to Sunday column
  const { weeks, monthLabels } = useMemo(() => {
    const startDate = new Date(targetYear, 0, 1);
    // Align to the previous Sunday so col 0 = Sunday
    const startDayOfWeek = startDate.getDay(); // 0=Sun
    startDate.setDate(startDate.getDate() - startDayOfWeek);

    const weeksArr: (string | null)[][] = [];
    const monthLabelMap: Record<number, string> = {};

    const cursor = new Date(startDate);

    for (let w = 0; w < 53; w++) {
      const week: (string | null)[] = [];
      for (let d = 0; d < 7; d++) {
        const yyyy = cursor.getFullYear();
        const mm = String(cursor.getMonth() + 1).padStart(2, '0');
        const dd = String(cursor.getDate()).padStart(2, '0');
        const dateStr = `${yyyy}-${mm}-${dd}`;

        // Only include dates in the target year
        if (cursor.getFullYear() === targetYear) {
          week.push(dateStr);
          // Track month label at week start (first day of a new month in col 0)
          if (cursor.getDate() <= 7 || (d === 0 && cursor.getDate() < 14)) {
            if (!Object.values(monthLabelMap).includes(MONTHS[cursor.getMonth()])) {
              monthLabelMap[w] = MONTHS[cursor.getMonth()];
            }
          }
        } else {
          week.push(null);
        }

        cursor.setDate(cursor.getDate() + 1);
      }
      // Only push weeks that have at least one day in the target year
      if (week.some((d) => d !== null)) {
        weeksArr.push(week);
      }
    }

    return { weeks: weeksArr, monthLabels: monthLabelMap };
  }, [targetYear]);

  // Stats computation
  const stats = useMemo(() => {
    const days = Object.values(dataMap);
    if (days.length === 0) return null;

    const yearTotal = days.reduce((sum, d) => sum + d.pnl, 0);
    const best = Math.max(...days.map((d) => d.pnl));
    const worst = Math.min(...days.map((d) => d.pnl));
    const winDays = days.filter((d) => d.pnl > 0).length;
    const winPct = days.length > 0 ? (winDays / days.length) * 100 : 0;

    return { yearTotal, best, worst, winPct };
  }, [dataMap]);

  const CELL = 10;
  const GAP = 2;

  return (
    <div style={{ fontFamily: 'monospace', fontSize: '11px' }}>
      {/* Month labels row */}
      <div style={{ display: 'flex', marginLeft: '22px', marginBottom: '4px', gap: `${GAP}px` }}>
        {weeks.map((_, wi) => (
          <div
            key={wi}
            style={{ width: CELL, flexShrink: 0, color: '#6b7280', fontSize: '10px', textAlign: 'center' }}
          >
            {monthLabels[wi] ?? ''}
          </div>
        ))}
      </div>

      {/* Main grid: day-of-week labels + cells */}
      <div style={{ display: 'flex', gap: '6px' }}>
        {/* Day of week labels */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: `${GAP}px`, justifyContent: 'flex-start' }}>
          {DAYS_OF_WEEK.map((label, i) => (
            <div
              key={i}
              style={{
                height: CELL,
                width: 14,
                display: 'flex',
                alignItems: 'center',
                color: '#6b7280',
                fontSize: '9px',
              }}
            >
              {label}
            </div>
          ))}
        </div>

        {/* Weeks */}
        <div style={{ display: 'flex', gap: `${GAP}px` }}>
          {weeks.map((week, wi) => (
            <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: `${GAP}px` }}>
              {week.map((dateStr, di) => {
                const dayData = dateStr ? dataMap[dateStr] : null;
                const isToday = dateStr === new Date().toISOString().slice(0, 10);

                return (
                  <div
                    key={di}
                    style={{
                      width: CELL,
                      height: CELL,
                      borderRadius: '2px',
                      backgroundColor: dateStr
                        ? getPnlColor(dayData ? dayData.pnlPct : null)
                        : 'transparent',
                      cursor: dayData ? 'pointer' : 'default',
                      outline: isToday ? '1px solid #00ff88' : 'none',
                      outlineOffset: '1px',
                      transition: 'transform 0.1s',
                    }}
                    onMouseEnter={(e) => {
                      if (!dayData || !dateStr) return;
                      const rect = (e.target as HTMLElement).getBoundingClientRect();
                      setTooltip({
                        date: dateStr,
                        pnl: dayData.pnl,
                        pnlPct: dayData.pnlPct,
                        trades: dayData.trades,
                        x: rect.right,
                        y: rect.top,
                      });
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '10px', marginLeft: '22px' }}>
        <span style={{ color: '#6b7280', fontSize: '10px' }}>Loss</span>
        {LEGEND_STEPS.map((step, i) => (
          <div
            key={i}
            style={{
              width: 10,
              height: 10,
              borderRadius: '2px',
              backgroundColor: step.color,
            }}
          />
        ))}
        <span style={{ color: '#6b7280', fontSize: '10px' }}>Profit</span>
      </div>

      {/* Stats bar */}
      {stats && (
        <div
          style={{
            display: 'flex',
            gap: '20px',
            marginTop: '10px',
            marginLeft: '22px',
            fontSize: '11px',
            color: '#9ca3af',
          }}
        >
          <span>
            Year P&amp;L:{' '}
            <span style={{ color: stats.yearTotal >= 0 ? '#00ff88' : '#ff4444', fontWeight: 700 }}>
              {stats.yearTotal >= 0 ? '+' : ''}${stats.yearTotal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </span>
          <span>
            Best day:{' '}
            <span style={{ color: '#00ff88', fontWeight: 700 }}>
              +${stats.best.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </span>
          <span>
            Worst:{' '}
            <span style={{ color: '#ff4444', fontWeight: 700 }}>
              -${Math.abs(stats.worst).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </span>
          <span>
            Win days:{' '}
            <span style={{ color: stats.winPct >= 50 ? '#00ff88' : '#ff4444', fontWeight: 700 }}>
              {stats.winPct.toFixed(0)}%
            </span>
          </span>
        </div>
      )}

      {/* Tooltip rendered via fixed positioning */}
      {tooltip && <CalendarTooltip tip={tooltip} />}
    </div>
  );
}
