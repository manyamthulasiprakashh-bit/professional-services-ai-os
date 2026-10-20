/** Safe public fields of the authenticated caller (never includes secrets). */
export interface CurrentUser {
  id: number
  username: string
  role: string
}
