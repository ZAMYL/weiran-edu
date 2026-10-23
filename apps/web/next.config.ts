import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Keep framework-generated agent rules from replacing the team's own instructions.
  agentRules: false,
  devIndicators: false,
};

export default nextConfig;
