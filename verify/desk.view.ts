export const title = "A CityWheels booking at the desk"

const money: Record<string, string> = {
  none: "No quote yet",
  full: "Full refund quoted",
  half: "Half refund quoted",
  deposit: "Deposit only quoted",
  nothing: "Nothing refunded",
}

export const state = (v: {
  quote: string
  changeResult: string
}) => {
  const quoted = money[v.quote] ?? v.quote
  if (v.changeResult === "yes") return `${quoted}, and a change was allowed`
  if (v.changeResult === "no") return `${quoted}, and a change was refused`
  return quoted
}

const cancelLabel: Record<string, string> = {
  full: "you ask to cancel, and the quote is the full rental plus the deposit",
  half: "you ask to cancel, and the quote is half the rental plus the deposit",
  deposit: "you ask to cancel, and the quote is the deposit only",
  nothing: "you ask to cancel, and nothing is refunded",
}

export const step = (action: string, _from: { quote: string }, to: { quote: string }) => {
  if (action === "Done" || action === "TimePasses" || action === "RentalStarts" || action === "RentalEnds") {
    return null
  }
  if (action === "AskCancel") return cancelLabel[to.quote] ?? "you ask to cancel"
  if (action === "AskChange") return "you ask to change the booking"
  if (action === "MarkCancelled") return "the booking is already cancelled"
  return action
}

export const group = (v: { quote: string }) => {
  if (v.quote === "nothing") return "blocked"
  if (v.quote === "none") return "waiting"
  return "quoted"
}

export const colors = {
  waiting: "blue",
  quoted: "teal",
  blocked: "orange",
}
