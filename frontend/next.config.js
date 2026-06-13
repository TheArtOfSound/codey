/** @type {import('next').NextConfig} */
const isStaticExport =
  process.env.NEXT_PUBLIC_STATIC_EXPORT === "true" ||
  process.env.NEXT_PUBLIC_GITHUB_PAGES === "true";

const isGitHubPages = process.env.NEXT_PUBLIC_GITHUB_PAGES === "true";
const repoBasePath = "/codey";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: isStaticExport ? "export" : undefined,
  basePath: isGitHubPages ? repoBasePath : undefined,
  assetPrefix: isGitHubPages ? `${repoBasePath}/` : undefined,
  trailingSlash: isStaticExport ? true : undefined,
  images: {
    unoptimized: isStaticExport,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "avatars.githubusercontent.com",
      },
    ],
  },
};

if (!isStaticExport) {
  nextConfig.redirects = async function redirects() {
    return [
      {
        source: "/login",
        destination: "/auth/login",
        permanent: true,
      },
      {
        source: "/signup",
        destination: "/auth/signup",
        permanent: true,
      },
      {
        source: "/dashboard/settings",
        destination: "/settings",
        permanent: true,
      },
      {
        source: "/dashboard/repositories",
        destination: "/dashboard/repos",
        permanent: true,
      },
      {
        source: "/dashboard/onboarding",
        destination: "/onboarding",
        permanent: true,
      },
    ];
  };

  nextConfig.rewrites = async function rewrites() {
    const apiUrl = process.env.NEXT_SERVER_API_URL || "http://localhost:8000";

    return [
      {
        source: "/api/proxy/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  };
}

module.exports = nextConfig;
