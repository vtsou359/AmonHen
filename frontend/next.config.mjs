/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the production image small: Next traces exactly the
  // node_modules it actually needs instead of shipping the whole tree.
  output: "standalone",
  transpilePackages: ["@deck.gl/core", "@deck.gl/layers", "@deck.gl/mapbox"],
};

export default nextConfig;
