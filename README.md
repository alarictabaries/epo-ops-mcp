### Vibe coded MCP server for EPO OPS

Create a `.env` file in the project root with your EPO OPS credentials:

```bash
OPS_ID=your_consumer_key
OPS_SECRET=your_consumer_secret
```

Add the following config to your MCP client
```json
"ops-epo": {
  "command": "python",
  "args": ["server.py"]
}
```
