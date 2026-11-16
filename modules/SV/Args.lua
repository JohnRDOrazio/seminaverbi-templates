-- Arguments of an {{#invoke:}}: the calling template's arguments, overlaid by the invoke's own,
-- trimmed, with blank values dropped.
local p = {}

function p.get(frame)
	local args = {}
	local function add(source)
		for k, v in pairs(source or {}) do
			v = mw.text.trim(v)
			if v ~= '' then
				args[k] = v
			else
				args[k] = nil
			end
		end
	end
	local parent = frame.getParent and frame:getParent()
	if parent then
		add(parent.args)
	end
	add(frame.args)
	return args
end

return p
