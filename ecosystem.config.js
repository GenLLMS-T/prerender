require('dotenv').config();

const instances = parseInt(process.env.PM2_INSTANCES, 10);
const maxMemory = process.env.PM2_MAX_MEMORY_RESTART || '1G';

module.exports = {
  apps: [
    {
      name: 'prerender',
      script: 'server.js',
      instances: Number.isNaN(instances) ? 1 : instances,
      autorestart: true,
      watch: false,
      max_memory_restart: '2G',
      env: {
        NODE_ENV: process.env.NODE_ENV || 'development',
        PORT: process.env.PORT || 3081,
      },
      env_production: {
        NODE_ENV: 'production',
        PORT: process.env.PORT || 3081,
      },
    },
  ],
};
