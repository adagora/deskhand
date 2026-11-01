export const title = "One question through the CityWheels agent loop"

export const state = (v: { phase: string; round: number }) => {
  if (v.phase === "answered") return "The model answered"
  if (v.phase === "staff") return "Staff message"
  if (v.phase === "tools") return `Running tools in round ${v.round}`
  return v.round === 0 ? "Waiting for the model" : `Model reads results after round ${v.round}`
}

export const step = (action: string) => {
  if (action === "Done" || action === "ToolReturns" || action === "ToolFails") return null
  if (action === "ModelWritesText") return "the model writes an answer"
  if (action === "ModelWritesNothing") return "the model writes nothing"
  if (action === "ModelAsksForTools") return "the model asks for tools"
  if (action === "ModelAsksWithoutTools") return "the model asks for tools in the last round"
  if (action === "RoundEnds") return "every tool returned"
  return action
}

export const group = (v: { phase: string }) => {
  if (v.phase === "answered") return "answered"
  if (v.phase === "staff") return "staff"
  return "working"
}

export const colors = { working: "blue", answered: "teal", staff: "orange" }
