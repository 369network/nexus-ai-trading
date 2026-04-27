'use client';

import { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { cn } from '@/lib/utils';
import type { AgentDecision, AgentRole } from '@/lib/types';

interface AgentNode {
  id: AgentRole;
  label: string;
  description: string;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
  vx?: number;
  vy?: number;
}

interface AgentEdge {
  source: AgentRole | AgentNode;
  target: AgentRole | AgentNode;
  label: string;
  strength: number;
}

const AGENT_NODES: AgentNode[] = [
  { id: 'bull', label: 'Bull', description: 'Long opportunities' },
  { id: 'bear', label: 'Bear', description: 'Short opportunities' },
  { id: 'fundamental', label: 'Fundamental', description: 'Macro analysis' },
  { id: 'technical', label: 'Technical', description: 'Chart patterns' },
  { id: 'sentiment', label: 'Sentiment', description: 'News & positioning' },
  { id: 'risk', label: 'Risk Mgr', description: 'Portfolio risk' },
  { id: 'portfolio', label: 'Portfolio', description: 'Final decision' },
];

const AGENT_EDGES: AgentEdge[] = [
  { source: 'bull', target: 'portfolio', label: 'vote', strength: 0.8 },
  { source: 'bear', target: 'portfolio', label: 'vote', strength: 0.8 },
  { source: 'fundamental', target: 'bull', label: 'macro', strength: 0.6 },
  { source: 'fundamental', target: 'bear', label: 'macro', strength: 0.6 },
  { source: 'technical', target: 'bull', label: 'signal', strength: 0.7 },
  { source: 'technical', target: 'bear', label: 'signal', strength: 0.7 },
  { source: 'sentiment', target: 'bull', label: 'sentiment', strength: 0.5 },
  { source: 'sentiment', target: 'bear', label: 'sentiment', strength: 0.5 },
  { source: 'risk', target: 'portfolio', label: 'risk check', strength: 0.9 },
  { source: 'portfolio', target: 'risk', label: 'size request', strength: 0.7 },
];

function getNodeColor(role: AgentRole, decision?: AgentDecision): string {
  if (!decision) return '#4b5563';
  if (decision.decision === 'LONG') return '#00ff88';
  if (decision.decision === 'SHORT') return '#ff4444';
  return '#ffaa00';
}

interface AgentNetworkProps {
  agentStates: Partial<Record<AgentRole, AgentDecision>>;
  onAgentClick?: (role: string) => void;
}

export function AgentNetwork({ agentStates, onAgentClick }: AgentNetworkProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const simulationRef = useRef<d3.Simulation<AgentNode, AgentEdge> | null>(null);
  const [selectedNode, setSelectedNode] = useState<AgentRole | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: AgentNode } | null>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return;

    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight || 400;

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height);

    svg.selectAll('*').remove();

    // Defs: arrowheads
    const defs = svg.append('defs');
    ['long', 'short', 'neutral', 'default'].forEach((type) => {
      const colors: Record<string, string> = {
        long: '#00ff88', short: '#ff4444', neutral: '#ffaa00', default: '#2a2a3e',
      };
      defs.append('marker')
        .attr('id', `arrow-${type}`)
        .attr('viewBox', '0 -4 8 8')
        .attr('refX', 14)
        .attr('refY', 0)
        .attr('markerWidth', 6)
        .attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-4L8,0L0,4')
        .attr('fill', colors[type])
        .attr('opacity', 0.6);
    });

    const nodes: AgentNode[] = AGENT_NODES.map((n) => ({ ...n }));
    const edges: AgentEdge[] = AGENT_EDGES.map((e) => ({ ...e }));

    // Force simulation
    const simulation = d3.forceSimulation<AgentNode>(nodes)
      .force('link', d3.forceLink<AgentNode, AgentEdge>(edges)
        .id((d) => d.id)
        .distance(120)
        .strength((d) => d.strength * 0.5)
      )
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(50));

    simulationRef.current = simulation;

    // Draw edges
    const linkGroup = svg.append('g').attr('class', 'links');
    const link = linkGroup.selectAll('line')
      .data(edges)
      .enter()
      .append('line')
      .attr('stroke', '#2a2a3e')
      .attr('stroke-width', (d) => d.strength * 2)
      .attr('stroke-opacity', 0.6)
      .attr('marker-end', 'url(#arrow-default)');

    // Draw nodes
    const nodeGroup = svg.append('g').attr('class', 'nodes');
    const node = nodeGroup.selectAll<SVGGElement, AgentNode>('g')
      .data(nodes)
      .enter()
      .append('g')
      .attr('class', 'node')
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGGElement, AgentNode>()
          .on('start', (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('end', (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      )
      .on('click', (event, d) => {
        setSelectedNode(d.id);
        onAgentClick?.(d.id);
      })
      .on('mouseover', (event, d) => {
        const rect = containerRef.current?.getBoundingClientRect();
        if (rect) {
          setTooltip({
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
            node: d,
          });
        }
      })
      .on('mouseout', () => {
        setTooltip(null);
      });

    // Outer glow ring
    node.append('circle')
      .attr('r', 36)
      .attr('fill', 'none')
      .attr('stroke', (d) => getNodeColor(d.id, agentStates[d.id]))
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.2)
      .attr('class', 'glow-ring');

    // Main circle
    node.append('circle')
      .attr('r', (d) => {
        const conf = agentStates[d.id]?.confidence ?? 0.5;
        return 22 + conf * 14;
      })
      .attr('fill', (d) => {
        const color = getNodeColor(d.id, agentStates[d.id]);
        return `${color}20`;
      })
      .attr('stroke', (d) => getNodeColor(d.id, agentStates[d.id]))
      .attr('stroke-width', 2)
      .style('transition', 'all 0.5s ease');

    // Label
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('font-size', '11px')
      .attr('font-weight', '600')
      .attr('fill', '#e2e8f0')
      .text((d) => d.label);

    // Confidence badge
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '1.8em')
      .attr('font-size', '9px')
      .attr('fill', (d) => getNodeColor(d.id, agentStates[d.id]))
      .text((d) => {
        const conf = agentStates[d.id]?.confidence;
        return conf ? `${(conf * 100).toFixed(0)}%` : '';
      });

    // Tick update
    simulation.on('tick', () => {
      link
        .attr('x1', (d) => (d.source as AgentNode).x ?? 0)
        .attr('y1', (d) => (d.source as AgentNode).y ?? 0)
        .attr('x2', (d) => (d.target as AgentNode).x ?? 0)
        .attr('y2', (d) => (d.target as AgentNode).y ?? 0);

      node.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => {
      simulation.stop();
    };
  }, []); // Init once

  // Update node colors when agentStates change
  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);

    svg.selectAll<SVGCircleElement, AgentNode>('.node circle:nth-child(2)')
      .attr('fill', (d) => `${getNodeColor(d.id, agentStates[d.id])}20`)
      .attr('stroke', (d) => getNodeColor(d.id, agentStates[d.id]));

    svg.selectAll<SVGTextElement, AgentNode>('.node text:nth-child(3)')
      .attr('fill', (d) => getNodeColor(d.id, agentStates[d.id]))
      .text((d) => {
        const conf = agentStates[d.id]?.confidence;
        return conf ? `${(conf * 100).toFixed(0)}%` : '';
      });
  }, [agentStates]);

  return (
    <div ref={containerRef} className="relative w-full h-full">
      <div className="absolute top-3 left-3 text-sm font-medium text-white">
        Agent Network
      </div>
      <div className="absolute top-3 right-3 flex items-center gap-3 text-xs">
        {[
          { color: '#00ff88', label: 'Long' },
          { color: '#ff4444', label: 'Short' },
          { color: '#ffaa00', label: 'Neutral' },
          { color: '#4b5563', label: 'Pending' },
        ].map((item) => (
          <div key={item.label} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: item.color }} />
            <span className="text-muted">{item.label}</span>
          </div>
        ))}
      </div>

      <svg ref={svgRef} className="w-full h-full" />

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute pointer-events-none z-10 bg-card border border-border rounded-lg p-3 text-xs shadow-lg"
          style={{
            left: tooltip.x + 12,
            top: tooltip.y - 40,
            minWidth: 160,
          }}
        >
          <div className="font-bold text-white mb-1 capitalize">{tooltip.node.label}</div>
          <div className="text-muted mb-2">{tooltip.node.description}</div>
          {agentStates[tooltip.node.id] && (
            <>
              <div className="flex justify-between">
                <span className="text-muted">Decision:</span>
                <span
                  className={cn(
                    'font-bold',
                    agentStates[tooltip.node.id]?.decision === 'LONG' ? 'text-nexus-green' :
                    agentStates[tooltip.node.id]?.decision === 'SHORT' ? 'text-nexus-red' :
                    'text-nexus-yellow'
                  )}
                >
                  {agentStates[tooltip.node.id]?.decision}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Confidence:</span>
                <span className="text-nexus-blue">
                  {((agentStates[tooltip.node.id]?.confidence ?? 0) * 100).toFixed(0)}%
                </span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
