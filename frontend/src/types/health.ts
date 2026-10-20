/** Response contract for the backend's GET /health endpoint. */
export interface HealthResponse {
  status: string
  application: string
  version: string
}
