/** @type {import('next').NextConfig} */
const nextConfig = {
  // Next.js 14 — appDir is stable, no longer experimental
  env: {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://nykppjttxexgrmtnxvje.supabase.co',
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im55a3BwanR0eGV4Z3JtdG54dmplIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzcyOTA3NjksImV4cCI6MjA5Mjg2Njc2OX0.QD6oy_wJrTC1DHlftSwfr_evIgTeJIKgaI3zIK9dRBg',
    NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV || 'production',
    NEXT_PUBLIC_APP_NAME: 'NEXUS ALPHA',
  },
  webpack: (config) => {
    // Optimize for real-time updates with lightweight-charts
    config.resolve.alias = {
      ...config.resolve.alias,
    };
    return config;
  },
  // Enable compression for faster loads
  compress: true,
  // Optimize images
  images: {
    domains: [],
    formats: ['image/avif', 'image/webp'],
  },
  // Headers for real-time performance
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-XSS-Protection', value: '1; mode=block' },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
