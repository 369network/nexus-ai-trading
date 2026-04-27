/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'nexus-green': '#00ff88',
        'nexus-red': '#ff4444',
        'nexus-blue': '#0088ff',
        'nexus-yellow': '#ffaa00',
        'nexus-purple': '#8844ff',
        'nexus-cyan': '#00ccff',
        background: '#0a0a0f',
        card: '#12121a',
        'card-hover': '#1a1a28',
        border: '#1e1e2e',
        'border-bright': '#2a2a3e',
        muted: '#6b7280',
        'muted-foreground': '#9ca3af',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-green': 'pulseGreen 2s ease-in-out infinite',
        'pulse-red': 'pulseRed 2s ease-in-out infinite',
        'slide-in': 'slideIn 0.3s ease-out',
        'fade-in': 'fadeIn 0.2s ease-out',
        'number-tick': 'numberTick 0.3s ease-out',
        'glow': 'glow 2s ease-in-out infinite',
      },
      keyframes: {
        pulseGreen: {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(0, 255, 136, 0.4)' },
          '50%': { boxShadow: '0 0 0 8px rgba(0, 255, 136, 0)' },
        },
        pulseRed: {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(255, 68, 68, 0.4)' },
          '50%': { boxShadow: '0 0 0 8px rgba(255, 68, 68, 0)' },
        },
        slideIn: {
          from: { transform: 'translateY(-10px)', opacity: '0' },
          to: { transform: 'translateY(0)', opacity: '1' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        numberTick: {
          from: { transform: 'translateY(-100%)', opacity: '0' },
          to: { transform: 'translateY(0)', opacity: '1' },
        },
        glow: {
          '0%, 100%': { textShadow: '0 0 4px currentColor' },
          '50%': { textShadow: '0 0 12px currentColor, 0 0 24px currentColor' },
        },
      },
      backgroundImage: {
        'gradient-nexus': 'linear-gradient(135deg, #0a0a0f 0%, #12121a 100%)',
        'gradient-card': 'linear-gradient(145deg, #12121a 0%, #0e0e18 100%)',
        'gradient-green': 'linear-gradient(135deg, #00ff88 0%, #00cc6a 100%)',
        'gradient-red': 'linear-gradient(135deg, #ff4444 0%, #cc2222 100%)',
      },
      boxShadow: {
        'nexus-green': '0 0 20px rgba(0, 255, 136, 0.15)',
        'nexus-red': '0 0 20px rgba(255, 68, 68, 0.15)',
        'nexus-blue': '0 0 20px rgba(0, 136, 255, 0.15)',
        'card': '0 4px 24px rgba(0, 0, 0, 0.4)',
        'card-hover': '0 8px 32px rgba(0, 0, 0, 0.6)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
  ],
};
